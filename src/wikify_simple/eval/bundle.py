"""Load a wiki bundle from disk into typed objects.

A bundle is a directory:

    data/wikis/{name}/
      articles/*.md
      people/*.md
      _run.json        # optional, used by hit_rate

Each page file is markdown with a YAML frontmatter block:

    ---
    id: concept-photocatalysis
    kind: article
    title: Photocatalysis
    aliases: [photo-catalysis]
    links: [concept-tio2, person-fujishima]
    ---

    # Photocatalysis

    Photocatalysis is ... [^e1]

    ## Evidence

    [^e1]: chunk_abc12 (doc_xyz, p.3) > "Photocatalysis refers to ..."

This loader is intentionally tiny: stdlib only, no PyYAML dependency. The
frontmatter parser handles the small subset we actually emit (scalar
strings, scalar lists). If we ever need richer YAML, swap to PyYAML in
one place.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# --- types ---------------------------------------------------------------


@dataclass
class Evidence:
    marker: str  # e.g. "e1"
    chunk_id: str
    doc_id: str
    quote: str
    locator: str = ""  # "p.3", "slide 4", ...


@dataclass
class Page:
    id: str
    kind: str  # "article" | "person"
    title: str
    aliases: list[str]
    links: list[str]
    body_clean: str  # body with frontmatter, evidence block, and
    # any "## References" / "## Boilerplate" stripped
    evidence: list[Evidence]
    path: Path  # source file
    provenance: dict = field(default_factory=dict)  # from sidecar JSON


@dataclass
class Bundle:
    name: str
    root: Path
    pages: list[Page]
    run_meta: dict = field(default_factory=dict)  # parsed _run.json

    # convenience views
    @property
    def concepts(self) -> list[Page]:
        return [p for p in self.pages if p.kind == "article"]

    @property
    def people(self) -> list[Page]:
        return [p for p in self.pages if p.kind == "person"]

    def by_id(self, page_id: str) -> Page | None:
        for p in self.pages:
            if p.id == page_id:
                return p
        return None


# --- frontmatter parser --------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)

# Strip these sections (and everything until the next H2 or EOF) from the body.
_STRIP_SECTIONS = {"evidence", "references", "boilerplate"}


def _parse_scalar(value: str) -> str | list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    return value.strip('"').strip("'")


def _parse_frontmatter(fm: str) -> dict:
    out: dict = {}
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = _parse_scalar(value)
    return out


# --- evidence + body extraction ------------------------------------------

# Evidence lines are structurally:
#
#     [^MARKER]: <chunk_id> (<doc_id>[, <locator>]) > "<quote>"
#
# chunk_id and doc_id can contain spaces, brackets, and punctuation
# (real corpora use human-readable stems like "[2018 Yang] Paper_hash__c0069").
# So we anchor on the stable parts:
#   - "[^...]: " at the start (marker),
#   - ' > "' as the separator before the quote,
#   - a trailing quote that closes the evidence value.
# Between marker and ' > "' lives "<chunk_id> (<doc_id>[, locator])". We take
# the LAST " (" before the final ") " as the chunk/doc boundary, and the LAST
# ")" before ' > "' closes the doc parens. Quotes may be straight or curly.
_MARKER_PREFIX_RE = re.compile(r"^\[\^(?P<marker>[^\]]+)\]:\s*(?P<rest>.*)$", re.DOTALL)
_QUOTE_CHARS = "\"\u201c\u201d\u2018\u2019'"


def _normalize_quotes(s: str) -> str:
    return (
        s.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _parse_evidence_value(rest: str) -> tuple[str, str, str, str] | None:
    """Parse the post-marker text into (chunk_id, doc_id, locator, quote).

    Supports two formats:
      Alternate:  ``<chunk_id> (<doc_id>[, locator]) > "quote"``
      Current: ``<doc_id>[, locator] > "quote"``

    ``rest`` is everything after ``[^e1]: `` on the evidence line (possibly
    continued across following lines if the quote spans multiple lines).
    """
    rest = _normalize_quotes(rest).strip()
    # Find the ' > "' separator (tolerant of whitespace around '>').
    sep_match = re.search(r'\s>\s*"', rest)
    if not sep_match:
        return None
    head = rest[: sep_match.start()].strip()
    tail = rest[sep_match.end() :]
    # Quote is everything up to the final '"' in tail; collapse internal
    # whitespace/newlines to single spaces so multi-line quotes become one.
    close = tail.rfind('"')
    if close == -1:
        quote = tail
    else:
        quote = tail[:close]
    quote = " ".join(quote.split())

    # Try alternate format: head ends with ")" meaning "<chunk_id> (<doc_id>[, loc])"
    if head.endswith(")"):
        depth = 0
        open_idx = -1
        for i in range(len(head) - 1, -1, -1):
            c = head[i]
            if c == ")":
                depth += 1
            elif c == "(":
                depth -= 1
                if depth == 0:
                    open_idx = i
                    break
        if open_idx > 0:
            chunk_id = head[:open_idx].strip()
            inner = head[open_idx + 1 : -1].strip()
            if chunk_id and inner:
                locator = ""
                doc_id = inner
                last_comma = inner.rfind(",")
                if last_comma != -1:
                    candidate = inner[last_comma + 1 :].strip()
                    if re.match(
                        r"^(p\.?\s*\d|pp\.?\s*\d|slide\s*\d|fig\.?\s*\d|sec\.?\s*\d)",
                        candidate,
                        re.IGNORECASE,
                    ):
                        doc_id = inner[:last_comma].strip()
                        locator = candidate
                return chunk_id, doc_id, locator, quote

    # Current format: head is just "<doc_id>[, locator]" (no chunk_id).
    # Use doc_id as chunk_id placeholder.
    locator = ""
    doc_id = head
    last_comma = head.rfind(",")
    if last_comma != -1:
        candidate = head[last_comma + 1 :].strip()
        if re.match(
            r"^(p\.?\s*\d|pp\.?\s*\d|slide\s*\d|fig\.?\s*\d|sec\.?\s*\d)",
            candidate,
            re.IGNORECASE,
        ):
            doc_id = head[:last_comma].strip()
            locator = candidate
    if doc_id:
        return doc_id, doc_id, locator, quote

    return None


_H2_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _split_h2_sections(body: str) -> list[tuple[str, str]]:
    """Return [(section_title, section_text)]; first item has title ''."""
    matches = list(_H2_RE.finditer(body))
    if not matches:
        return [("", body)]
    sections = []
    if matches[0].start() > 0:
        sections.append(("", body[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        title = m.group("title").strip().lower()
        sections.append((title, body[m.end() : end]))
    return sections


def _extract_evidence(body: str) -> list[Evidence]:
    """Parse ``[^eN]: <chunk> (<doc>[, loc]) > "<quote>"`` lines.

    Tolerates: chunk_ids/doc_ids containing spaces, brackets, and commas;
    curly quotes; extra whitespace; and quotes that span multiple lines.
    """
    out: list[Evidence] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _MARKER_PREFIX_RE.match(line.strip())
        if not m:
            i += 1
            continue
        marker = m.group("marker")
        rest = m.group("rest")
        # If the quote is not closed on this line, accumulate until we see
        # another evidence marker, a blank line, or a closing quote.
        normalized = _normalize_quotes(rest)

        # Quote is "closed" if there is a ' > "' AND a following '"'.
        def _closed(s: str) -> bool:
            sep = re.search(r'\s>\s*"', s)
            if not sep:
                return False
            return s.rfind('"') > sep.end() - 1

        j = i
        while not _closed(normalized) and j + 1 < len(lines):
            nxt = lines[j + 1]
            # Stop if the next line starts another evidence marker.
            if _MARKER_PREFIX_RE.match(nxt.strip()):
                break
            normalized += "\n" + _normalize_quotes(nxt)
            j += 1
        parsed = _parse_evidence_value(normalized)
        if parsed is not None:
            chunk_id, doc_id, locator, quote = parsed
            out.append(
                Evidence(
                    marker=marker,
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    quote=quote,
                    locator=locator,
                )
            )
        i = j + 1
    return out


def _clean_body(body: str) -> str:
    """Drop frontmatter sections we don't want in the M1 embedding.

    Keeps: page intro and any prose H2 sections that aren't in
    _STRIP_SECTIONS. Drops: '## Evidence', '## References', '## Boilerplate'
    and their content.
    """
    sections = _split_h2_sections(body)
    keep = [text for title, text in sections if title not in _STRIP_SECTIONS]
    cleaned = "\n".join(keep).strip()
    return cleaned


# --- public loader -------------------------------------------------------


def _parse_page(path: Path) -> Page:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"page {path} has no YAML frontmatter")
    fm = _parse_frontmatter(m.group("fm"))
    body = m.group("body")
    evidence = _extract_evidence(body)
    body_clean = _clean_body(body)

    def _as_list(v) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return [str(v)]

    sidecar = path.with_suffix(".provenance.json")
    provenance: dict = {}
    if sidecar.exists():
        try:
            provenance = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            provenance = {}

    return Page(
        id=str(fm.get("id", path.stem)),
        kind=str(fm.get("kind", "article")),
        title=str(fm.get("title", path.stem)),
        aliases=_as_list(fm.get("aliases")),
        links=_as_list(fm.get("links")),
        body_clean=body_clean,
        evidence=evidence,
        path=path,
        provenance=provenance,
    )


def load_bundle(root: str | Path) -> Bundle:
    """Load all .md files under {root}/concepts and {root}/people."""
    root = Path(root)
    pages: list[Page] = []
    for sub in ("articles", "people"):
        d = root / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            pages.append(_parse_page(f))

    run_meta: dict = {}
    run_path = root / "_run.json"
    if run_path.exists():
        run_meta = json.loads(run_path.read_text(encoding="utf-8"))

    return Bundle(name=root.name, root=root, pages=pages, run_meta=run_meta)
