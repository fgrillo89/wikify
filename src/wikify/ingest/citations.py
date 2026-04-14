"""Extract bibliography entries from academic markdown.

Finds the references section, splits it into individual entries, and
extracts only the fields that are reliably extractable from raw text:
``doi``, ``year``, and ``author_last_names`` (for corpus-internal
matching).  Structured fields (``title``, ``authors``, ``venue``) are
populated later by CrossRef resolution, not by regex heuristics.
"""

import re

# --- regex constants ------------------------------------------------------

_REFS_HEADING_RE = re.compile(
    r"^(#{1,3})[^A-Za-z0-9\n]*(?:\d+[\d.]*\s*)?"
    r"(?:references?|bibliography|works\s+cited)"
    r"(?:\s+and\s+notes)?[^A-Za-z0-9\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
_NUMBERED_ENTRY_RE = re.compile(
    r"^\s*(?:-\s*)?(?:\[\d+\]|\(\d+\)|\d+[.)])\s+",
    re.MULTILINE,
)
_CITATION_LINE_RE = re.compile(
    r"^.{0,60}(?:[A-Z]\.\s*){1,4}[A-Z][a-z]+(?:[ \-][A-Z][a-z]+)?"
    r".*\b(19[5-9]\d|20[0-3]\d)\b",
    re.MULTILINE,
)

_MD_BOLD_RE = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_MD_ITALIC_RE = re.compile(r"_{1,2}(.+?)_{1,2}")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`+([^`]*)`+")

_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_DOI_RE = re.compile(r"\b(10\.\d{4,}/[^\s,;)\]]+)")

# Author last-name pattern: uppercase word of 2+ chars, not all-caps
# acronyms (IEEE, ACS), not common venue words.
_VENUE_WORDS = frozenset({
    "ieee", "acm", "proc", "proceedings", "trans", "journal", "phys",
    "rev", "mater", "nano", "nature", "science", "conference", "lett",
    "adv", "acs", "appl", "chem", "int", "commun", "rep", "vol",
})


def _clean_markdown(text: str) -> str:
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    return text.strip()


def _find_refs_section(md_text: str) -> str | None:
    match = _REFS_HEADING_RE.search(md_text)
    if match is not None:
        heading_level = len(match.group(1))
        start = match.end()
        next_heading_re = re.compile(
            rf"^#{{1,{heading_level}}}\s+\S",
            re.MULTILINE,
        )
        next_match = next_heading_re.search(md_text, start)
        end = next_match.start() if next_match else len(md_text)
        return md_text[start:end]
    # Fallback: scan the last ~40% for citation-line clusters.
    tail_start = int(len(md_text) * 0.6)
    tail = md_text[tail_start:]
    matches = list(_CITATION_LINE_RE.finditer(tail))
    if len(matches) < 3:
        return None
    first = matches[0].start()
    next_heading_re = re.compile(r"^#{1,3}\s+\S", re.MULTILINE)
    nxt = next_heading_re.search(tail, first)
    end = nxt.start() if nxt else len(tail)
    return tail[first:end]


def _split_entries(section_text: str) -> list[str]:
    if _NUMBERED_ENTRY_RE.search(section_text):
        parts = _NUMBERED_ENTRY_RE.split(section_text)
        return [p.strip() for p in parts[1:]]
    blocks = re.split(r"\n\s*\n", section_text)
    return [b.strip() for b in blocks]


# --- reliable field extraction --------------------------------------------


def _extract_doi(text: str) -> str | None:
    m = _DOI_RE.search(text)
    if not m:
        return None
    return m.group(1).rstrip(".,;)")


def _extract_year(text: str) -> int | None:
    m = _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_author_last_names(text: str) -> list[str]:
    """Extract probable author last names from raw citation text.

    Lightweight heuristic: find capitalized words (2+ chars) that appear
    before the year and are not venue/journal keywords.  Used only for
    corpus-internal fuzzy matching, not for .bib output.
    """
    year_match = _YEAR_RE.search(text)
    # Only look at text before the first year mention (author block)
    scope = text[: year_match.start()] if year_match else text[:200]
    # Remove initials like "J." or "A. B." to avoid false matches
    scope = re.sub(r"\b[A-Z]\.(?:\s*[A-Z]\.)*", "", scope)
    words = re.findall(r"\b([A-Z][a-z]{2,})\b", scope)
    seen: set[str] = set()
    names: list[str] = []
    for w in words:
        low = w.lower()
        if low in _VENUE_WORDS or low in seen:
            continue
        seen.add(low)
        names.append(w)
    return names[:10]  # cap to avoid noise


# --- public API -----------------------------------------------------------


def extract_citations(md_text: str, doc_id: str) -> list:
    """Extract bibliography entries from academic paper markdown text.

    Returns a list of ``CitationEntry`` objects with ``ord``, ``raw_text``,
    ``year``, ``doi``, and ``author_last_names`` populated.

    Structured fields (``title``, ``authors``, ``venue``) are NOT
    populated here -- they are filled later by heuristic parsing and/or
    API resolution.
    Returns ``[]`` if no references section is detected.
    """
    from ..citestore.models import CitationEntry
    from ..citestore.parse import extract_doi as extract_doi_from_url

    section = _find_refs_section(md_text)
    if section is None:
        return []

    raw_entries = _split_entries(section)

    out: list[CitationEntry] = []
    for idx, raw in enumerate(raw_entries):
        cleaned = _clean_markdown(raw)
        if len(cleaned) < 20:
            continue
        truncated = cleaned[:1000]
        # Try both the local regex and URL-aware DOI extraction
        doi = _extract_doi(truncated) or extract_doi_from_url(truncated)
        out.append(
            CitationEntry(
                ord=idx,
                raw_text=truncated,
                year=_extract_year(truncated),
                doi=doi or "",
                author_last_names=_extract_author_last_names(truncated),
            )
        )
    return out
