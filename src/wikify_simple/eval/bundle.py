"""Load a wiki bundle from disk into typed objects.

A bundle is a directory:

    data/wikis/{name}/
      concepts/*.md
      people/*.md
      _run.json        # optional, used by hit_rate

Each page file is markdown with a YAML frontmatter block:

    ---
    id: concept-photocatalysis
    kind: concept
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

from __future__ import annotations

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
    kind: str  # "concept" | "person"
    title: str
    aliases: list[str]
    links: list[str]
    body_clean: str  # body with frontmatter, evidence block, and
    # any "## References" / "## Boilerplate" stripped
    evidence: list[Evidence]
    path: Path  # source file


@dataclass
class Bundle:
    name: str
    root: Path
    pages: list[Page]
    run_meta: dict = field(default_factory=dict)  # parsed _run.json

    # convenience views
    @property
    def concepts(self) -> list[Page]:
        return [p for p in self.pages if p.kind == "concept"]

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

_EVIDENCE_LINE_RE = re.compile(
    r"""
    ^\[\^(?P<marker>[^\]]+)\]:\s*       # [^e1]:
    (?P<chunk>\S+)\s*                   # chunk_abc12
    \((?P<doc>[^,)]+)(?:,\s*(?P<loc>[^)]+))?\)\s*   # (doc_xyz, p.3)
    >\s*"(?P<quote>.*)"\s*$             # > "quote"
    """,
    re.VERBOSE,
)

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
    out: list[Evidence] = []
    for line in body.splitlines():
        m = _EVIDENCE_LINE_RE.match(line.strip())
        if not m:
            continue
        out.append(
            Evidence(
                marker=m.group("marker"),
                chunk_id=m.group("chunk"),
                doc_id=m.group("doc").strip(),
                quote=m.group("quote"),
                locator=(m.group("loc") or "").strip(),
            )
        )
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

    return Page(
        id=str(fm.get("id", path.stem)),
        kind=str(fm.get("kind", "concept")),
        title=str(fm.get("title", path.stem)),
        aliases=_as_list(fm.get("aliases")),
        links=_as_list(fm.get("links")),
        body_clean=body_clean,
        evidence=evidence,
        path=path,
    )


def load_bundle(root: str | Path) -> Bundle:
    """Load all .md files under {root}/concepts and {root}/people."""
    root = Path(root)
    pages: list[Page] = []
    for sub in ("concepts", "people"):
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
