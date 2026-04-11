"""Extract structured bibliography entries from academic markdown.

Reference-section detector with a small heuristic parser that turns each raw entry
into a structured dict (``ord``, ``raw_text``, ``authors``, ``year``,
``title``, ``venue``, ``doi``). The structured fields are best-effort:
academic citation styles vary, so missing fields are simply left empty
or ``None``. Downstream code (deterministic author pages, library.bib)
treats all structured fields as optional.
"""

import re

# --- regex constants ------------------------------------------------------

_REFS_HEADING_RE = re.compile(
    r"^(#{1,3})\s*(references|bibliography|works cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NUMBERED_ENTRY_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+", re.MULTILINE)

_MD_BOLD_RE = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_MD_ITALIC_RE = re.compile(r"_{1,2}(.+?)_{1,2}")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`+([^`]*)`+")

# --- structured-parsing regexes ------------------------------------------

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_DOI_RE = re.compile(r"\b(10\.\d{4,}/[^\s,;)\]]+)")
# Author block heuristics: a sequence of "Lastname, F." or "F. Lastname"
# tokens up to the year. We split on commas and reassemble pairs that
# look like initials + surname.


def _clean_markdown(text: str) -> str:
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    return text.strip()


def _find_refs_section(md_text: str) -> str | None:
    match = _REFS_HEADING_RE.search(md_text)
    if match is None:
        return None
    heading_level = len(match.group(1))
    start = match.end()
    next_heading_re = re.compile(
        rf"^#{{1,{heading_level}}}\s+\S",
        re.MULTILINE,
    )
    next_match = next_heading_re.search(md_text, start)
    end = next_match.start() if next_match else len(md_text)
    return md_text[start:end]


def _split_entries(section_text: str) -> list[str]:
    if _NUMBERED_ENTRY_RE.search(section_text):
        parts = _NUMBERED_ENTRY_RE.split(section_text)
        return [p.strip() for p in parts[1:]]
    blocks = re.split(r"\n\s*\n", section_text)
    return [b.strip() for b in blocks]


# --- structured-field heuristics -----------------------------------------


def _extract_doi(text: str) -> str | None:
    m = _DOI_RE.search(text)
    if not m:
        return None
    return m.group(1).rstrip(".,;)")


def _extract_year(text: str) -> int | None:
    m = _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _split_author_block(block: str) -> list[str]:
    """Split a leading author block into individual author names.

    Handles ``Lastname, F., Other, G.`` and ``F. Lastname, G. Other``
    and ``Lastname F, Other G`` styles. Drops trailing ``et al.``.
    """
    block = block.strip().rstrip(",.")
    block = re.sub(r"\s+et\s+al\.?$", "", block, flags=re.IGNORECASE)
    if not block:
        return []
    block = block.replace(" and ", ", ").replace(";", ",")
    parts = [p.strip() for p in block.split(",") if p.strip()]
    names: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        nxt = parts[i + 1] if i + 1 < len(parts) else ""
        # "Lastname, F." or "Lastname, F. M." -> merge
        if nxt and re.match(r"^[A-Z](?:\.\s*[A-Z]\.?)*\.?$", nxt):
            names.append(f"{nxt} {part}")
            i += 2
            continue
        names.append(part)
        i += 1
    # Filter junk
    cleaned: list[str] = []
    for n in names:
        n = n.strip().rstrip(".,")
        if not n or len(n) < 2:
            continue
        if not n[0].isupper():
            continue
        if len(n.split()) > 6:
            continue
        cleaned.append(n)
    return cleaned


def _parse_structured(raw: str) -> tuple[list[str], int | None, str, str, str | None]:
    """Best-effort: return (authors, year, title, venue, doi) from a raw
    reference string. Empty/None when a field is not detected.
    """
    doi = _extract_doi(raw)
    year = _extract_year(raw)
    authors: list[str] = []
    title = ""
    venue = ""

    # Split on the first year occurrence: text before is usually author block
    if year is not None:
        m = _YEAR_RE.search(raw)
        if m:
            before = raw[: m.start()].strip().rstrip(".,()[]")
            after = raw[m.end() :].strip().lstrip(".,)] ")
            authors = _split_author_block(before)
            # Title is usually the next sentence-like fragment
            # Split on '.' but keep the first chunky piece as title.
            tail_parts = re.split(r"(?<=[a-z])\.\s+|\.\s+(?=[A-Z])", after, maxsplit=2)
            if tail_parts:
                title = tail_parts[0].strip().rstrip(".")
                if len(tail_parts) > 1:
                    venue = tail_parts[1].strip().rstrip(".")
    else:
        # No year → assume the whole thing is a title-ish blob
        title = raw.strip()

    return authors, year, title, venue, doi


def extract_citations(md_text: str, doc_id: str) -> list[dict]:
    """Extract bibliography entries from academic paper markdown text.

    Returns a list of plain dicts with keys:
    ``ord``, ``raw_text``, ``authors`` (list[str]), ``year`` (int|None),
    ``title``, ``venue``, ``doi``. Returns ``[]`` if no references
    section is detected.
    """
    section = _find_refs_section(md_text)
    if section is None:
        return []

    raw_entries = _split_entries(section)

    out: list[dict] = []
    for idx, raw in enumerate(raw_entries):
        cleaned = _clean_markdown(raw)
        if len(cleaned) < 20:
            continue
        truncated = cleaned[:1000]
        authors, year, title, venue, doi = _parse_structured(truncated)
        out.append(
            {
                "ord": idx,
                "raw_text": truncated,
                "authors": authors,
                "year": year,
                "title": title,
                "venue": venue,
                "doi": doi,
            }
        )
    return out
