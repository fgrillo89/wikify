"""Parse-time markdown cleanup.

Strips noise paragraphs, repeated running headers (journal name + page),
licensing notices, and leading journal-title H1/H2 headings that pymupdf
duplicates per page. This runs AFTER ``_strip_pdf_artifacts`` (citation
markers, bracket-wrap, dashes) and BEFORE section detection so spans
align with the cleaned text.

Re-uses ``_is_noise_paragraph`` from ``metadata.py`` plus a small set of
additional structural strippers.
"""

import re

from ..metadata import is_noise_paragraph

# Lines that look like running headers / page footers pymupdf duplicates
# on every page. We only strip them when they repeat verbatim 3+ times.
_PAGE_NUM_RE = re.compile(r"\bpp?\.\s*\d+|\bvol\.\s*\d+|\bp\.\s*\d+\s*$", re.IGNORECASE)

# Leading journal-title heading patterns we drop when they appear before
# any abstract / section heading (these are page-1 banners pymupdf
# captures as H1).
_JOURNAL_HEADING_RE = re.compile(
    r"^#{1,2}\s+(?:IEEE\b|ACS\b|Nature\b|Proceedings\b|Journal of\b|Phys\.|"
    r"Physical Review\b|Science\b|Cell\b|Advanced \w+|Applied Physics)",
    re.IGNORECASE,
)

# Boilerplate single lines (case-insensitive substring match)
_LINE_NOISE_SUBSTRINGS = (
    "authorized licensed use limited to",
    "downloaded on",
    "restrictions apply",
    "all rights reserved",
    "this article has been accepted",
    "personal use of this material",
    "permission to make digital",
    "published by",
    "accepted for publication",
    "manuscript received",
    "redistribution",
    "free of charge via the internet",
    "supporting information",
    "available free of charge",
)

# Single-line patterns matched by regex (copyright, DOI, volume/issue, URLs)
_LINE_NOISE_RE = re.compile(
    r"(?i)^(?:"
    r"(?:copyright\s*)?(?:\(c\)|©)\s*\d{4}"  # © 2010 ...
    r"|doi:\s*10\.\d{4,}"  # DOI: 10.1021/...
    r"|https?://(?:dx\.)?doi\.org/"  # https://doi.org/...
    r"|https?://pubs\.\w+\.org/"  # https://pubs.acs.org/...
    r"|vol\.?\s*\d+\s*[,|]\s*(?:no\.?\s*\d+|issue)"  # Vol 453 | No. 1
    r"|pp?\.?\s*\d+\s*[-–]\s*\d+"  # pp. 100-105
    r"|e?-?mail:"  # email: / E-mail:
    r"|received\s+\d{1,2}\s+\w+\s+\d{4}"  # Received 3 January 2010
    r"|revised\s+\d{1,2}\s+\w+\s+\d{4}"  # Revised 15 March 2010
    r"|published\s+(?:online\s+)?\d{1,2}\s+\w+\s+\d{4}"  # Published online ...
    r")",
)


def _is_running_header_candidate(line: str) -> bool:
    """A line that *could* be a running header: short, ALL CAPS or page-numbered."""
    s = line.strip()
    if not s or len(s) >= 80:
        return False
    if _PAGE_NUM_RE.search(s):
        return True
    # ALL CAPS line with at least 3 letters
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return True
    return False


def _strip_repeated_headers(md: str) -> str:
    """Drop lines that look like running headers AND repeat 3+ times verbatim."""
    lines = md.split("\n")
    counts: dict[str, int] = {}
    for ln in lines:
        if _is_running_header_candidate(ln):
            counts[ln.strip()] = counts.get(ln.strip(), 0) + 1
    repeated = {k for k, v in counts.items() if v >= 3}
    if not repeated:
        return md
    return "\n".join(ln for ln in lines if ln.strip() not in repeated)


def _strip_line_noise(md: str) -> str:
    """Drop individual lines that match licensing-notice substrings or regex patterns."""
    out: list[str] = []
    for ln in md.split("\n"):
        low = ln.lower()
        if any(s in low for s in _LINE_NOISE_SUBSTRINGS):
            continue
        stripped = ln.strip()
        if stripped and _LINE_NOISE_RE.match(stripped):
            continue
        out.append(ln)
    return "\n".join(out)


def _strip_leading_journal_heading(md: str) -> str:
    """Strip a leading journal-title H1/H2 if it appears before any
    abstract / introduction / numbered section heading.
    """
    lines = md.split("\n")
    for i, ln in enumerate(lines[:30]):
        s = ln.strip()
        if not s:
            continue
        if _JOURNAL_HEADING_RE.match(s):
            # Drop this line (and the immediately following blank line).
            del lines[i]
            if i < len(lines) and not lines[i].strip():
                del lines[i]
            return "\n".join(lines)
        # Stop scanning once we hit a real section heading.
        if re.match(r"(?i)^#+\s*(abstract|introduction|1\.?\s|i\.\s)", s):
            break
    return md


def _strip_noise_paragraphs(md: str) -> str:
    """Drop whole paragraphs that match the noise marker list."""
    paragraphs = re.split(r"\n\s*\n", md)
    kept = [p for p in paragraphs if not is_noise_paragraph(p)]
    return "\n\n".join(kept)


def clean_markdown_text(md: str) -> str:
    """Run the full parse-time cleanup pipeline.

    Order matters:
      1. line-level noise (licensing notices) — cheap, removes lines that
         would otherwise foul the running-header detector
      2. running-header dedup — needs the licensing junk gone first
      3. leading journal heading
      4. paragraph-level noise (must run last so paragraphs are coherent)
    """
    if not md:
        return md
    md = _strip_line_noise(md)
    md = _strip_repeated_headers(md)
    md = _strip_leading_journal_heading(md)
    md = _strip_noise_paragraphs(md)
    # Collapse the blank-line storms the strippers leave behind.
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"
