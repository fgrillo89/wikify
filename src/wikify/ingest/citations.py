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

# Heading patterns seen in the mvp20 corpus we need to match:
#   ## References          ## **References**          ## _**References**_
#   ## REFERENCES          ## REFERENCES AND NOTES    ## **REFERENCES AND NOTES**
#   ## REFERENCE           ## **6.0.REFERENCES**      ## Bibliography
# A permissive "decoration" class on both sides catches leading/trailing
# emphasis (``*``/``_``), numbered prefixes, and stray unicode glyphs.
_REFS_HEADING_RE = re.compile(
    r"^(#{1,3})[^A-Za-z0-9\n]*(?:\d+[\d.]*\s*)?"
    r"(?:references?|bibliography|works\s+cited)"
    r"(?:\s+and\s+notes)?[^A-Za-z0-9\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
# Accept many numbered styles as entry delimiters:
#   "[12]", "12.", "12)", "(12)", "- [12]", "- 12.", "- 12)"
_NUMBERED_ENTRY_RE = re.compile(
    r"^\s*(?:-\s*)?(?:\[\d+\]|\(\d+\)|\d+[.)])\s+",
    re.MULTILINE,
)

# Fallback: when no explicit heading is found, find a cluster of lines
# that each contain an "author-initials + surname + year" pattern. This
# catches the many citation styles that escape the numbered-entry
# heuristic: inline prose refs (Chua 1971), ``> (34)`` blockquote refs
# (ACS), ``- 19M. A. Lampert`` bullet-with-stuck-number refs, ``- 23)``
# closing-paren refs, or un-numbered author-year bullet lists (Wiley).
# The pattern is strict enough to avoid body prose and permissive enough
# to cross all of those formats.
_CITATION_LINE_RE = re.compile(
    r"^.{0,60}(?:[A-Z]\.\s*){1,4}[A-Z][a-z]+(?:[ \-][A-Z][a-z]+)?.*\b(19[5-9]\d|20[0-3]\d)\b",
    re.MULTILINE,
)

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
    # Fallback: scan the last ~40% of the document for a cluster of
    # numbered citation lines. This catches short-form papers (Nature
    # letters, IEEE one-column dense papers) where pymupdf4llm never
    # produces a ``## References`` heading but the numbered refs are
    # still in the body.
    tail_start = int(len(md_text) * 0.6)
    tail = md_text[tail_start:]
    matches = list(_CITATION_LINE_RE.finditer(tail))
    if len(matches) < 3:
        return None
    # Anchor at the first citation-line hit and take everything through
    # the end of the document (or the next top-level heading, whichever
    # comes first).
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
            names.append(f"{_format_initials(nxt)} {part}")
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


def _format_initials(value: str) -> str:
    letters = re.findall(r"[A-Z]", value)
    return " ".join(f"{letter}." for letter in letters)


def parse_reference(raw: str) -> tuple[list[str], int | None, str, str, str | None]:
    """Best-effort: return (authors, year, title, venue, doi) from a raw
    reference string. Empty/None when a field is not detected.
    """
    text = _clean_markdown(raw)
    text = re.split(r"\n\s*\n\s*\[\s*\d+\s*\]|\s+\[\s*\d+\s*\]", text, maxsplit=1)[0]
    text = re.sub(r"\s+", " ", text).strip()
    doi = _extract_doi(text)
    year = _extract_year(text)
    authors: list[str] = []
    title = ""
    venue = ""

    if year is not None:
        quoted = _parse_quoted_reference(text)
        if quoted is not None:
            authors, title, venue = quoted
            return authors, year, title, venue, doi
        m = _YEAR_RE.search(text)
        if m:
            before_raw = text[: m.start()].strip()
            year_in_parens = before_raw.rstrip().endswith("(")
            before = before_raw.rstrip(".,()[]")
            after = text[m.end() :].strip().lstrip(".,)] ")
            if year_in_parens and _looks_like_reference_title(after):
                authors = _split_author_block(before.rstrip("(").strip())
                title, venue = _split_title_venue(after)
            else:
                # ACS/IEEE styles usually put title before the year:
                # ``Authors. Title. Venue 2013, 24, 384009``.
                authors, title, venue = _split_before_year_reference(before)
                if not authors:
                    authors = _split_author_block(before)
                if not venue and after:
                    venue = after
    else:
        # No year → assume the whole thing is a title-ish blob
        parts = _split_reference_sentences(text)
        if len(parts) >= 2:
            authors = _split_author_block(parts[0])
            title = parts[1]
            venue = " ".join(parts[2:])
        else:
            title = text

    return authors, year, title, venue, doi


def _parse_structured(raw: str) -> tuple[list[str], int | None, str, str, str | None]:
    return parse_reference(raw)


def _split_reference_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[a-z0-9])\.\s+|\.\s+(?=[A-Z])", text)
    return [part.strip().rstrip(".") for part in parts if part.strip().rstrip(".")]


def _split_before_year_reference(before: str) -> tuple[list[str], str, str]:
    for match in re.finditer(r"\.\s+", before):
        author_block = before[: match.start()].strip()
        rest = before[match.end() :].strip()
        if _looks_like_venue_fragment(author_block):
            continue
        authors = _split_author_block(author_block)
        if not authors or not _looks_like_reference_title(rest):
            continue
        title, venue = _split_title_venue(rest)
        return authors, title, venue
    comma_result = _split_comma_reference(before)
    if comma_result[1]:
        return comma_result
    return [], "", ""


def _parse_quoted_reference(text: str) -> tuple[list[str], str, str] | None:
    match = re.search(
        r"['\"]\s*(?P<title>[A-Z][^'\"]{20,}?)\s*['\"]\s*,?\s*"
        r"(?P<venue>.*?)(?:\(|\b(?:19[5-9]\d|20[0-3]\d)\b)",
        text,
    )
    if not match:
        match = re.search(
            r"['\"]\s*(?P<title>[A-Z][^'\"]{20,}?)(?:,\s+(?P<venue>.*?))?"
            r"\s*(?:\(|\b(?:19[5-9]\d|20[0-3]\d)\b)",
            text,
        )
    if not match:
        return None
    title = match.group("title").strip(" ,.;")
    if not _looks_like_reference_title(title):
        return None
    authors = _split_author_block(text[: match.start()].strip(" ,.;"))
    venue = (match.group("venue") or "").strip(" ,.;")
    return authors, title, venue


def _split_comma_reference(before: str) -> tuple[list[str], str, str]:
    parts = [part.strip() for part in before.split(",") if part.strip()]
    author_parts: list[str] = []
    for idx, part in enumerate(parts):
        if _looks_like_author_part(part):
            author_parts.append(part)
            continue
        if _looks_like_venue_fragment(part):
            return _split_author_block(", ".join(author_parts)), "", ", ".join(parts[idx:])
        title = part
        venue = ", ".join(parts[idx + 1 :])
        return _split_author_block(", ".join(author_parts)), title, venue
    return _split_author_block(", ".join(author_parts)), "", ""


def _split_title_venue(text: str) -> tuple[str, str]:
    for match in re.finditer(r"(?<=[a-z0-9])\.\s+", text):
        title = text[: match.start()].strip().rstrip(".")
        venue = text[match.end() :].strip().rstrip(".")
        if _looks_like_reference_title(title):
            return title, venue
    return text.strip().rstrip("."), ""


def _looks_like_reference_title(text: str) -> bool:
    if re.match(r"^[a-z]", text.strip()):
        return False
    if re.match(r"^\d", text.strip()):
        return False
    if re.match(r"^[A-Z][A-Za-z' -]+,\s*[A-Z]\.?", text):
        return False
    if re.match(r"^[A-Z]\.?,\s*[A-Z][A-Za-z' -]+", text):
        return False
    if re.match(r"^[A-Z]\.\s+[A-Z][A-Za-z' -]+,", text):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z-]{2,}", text)
    if _looks_like_venue_fragment(text) and len(tokens) < 5:
        return False
    return len(tokens) >= 3


def _looks_like_author_part(text: str) -> bool:
    clean = text.strip()
    if not clean or _looks_like_venue_fragment(clean):
        return False
    if len(clean.split()) > 5:
        return False
    return bool(re.search(r"\b[A-Z]\.(?:[A-Z]\.)*|\b[A-Z]\.\s*[A-Z]\.", clean))


def _looks_like_venue_fragment(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:IEEE|ACM|Proc|Proceedings|Trans|Journal|Phys|Rev|"
            r"Mater|Nano|Nature|Science|Circuits|Electron|Devices|"
            r"Conference|Symp|Int\.?|Nat|Commun|Adv|Sci|Rep|ACS|Appl|Lett|"
            r"Nanoscale|Horiz|Angew|Chem|Front|Neurosci)\b",
            text,
        )
    )


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
