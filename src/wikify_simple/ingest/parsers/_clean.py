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
# Structural noise: substring phrases that never appear in real research
# prose — they always indicate licensing, access-control, or boilerplate
# metadata. We don't hardcode domain names; bare URLs are caught by the
# regex below.
_LINE_NOISE_SUBSTRINGS = (
    "authorized licensed use limited to",
    "downloaded on",
    "restrictions apply",
    "all rights reserved",
    "this article has been accepted",
    "personal use of this material",
    "permission to make digital",
    "accepted for publication",
    "redistribution",
    "free of charge via the internet",
    "supporting information",
    "available free of charge",
)

# Single-line patterns matched by regex. Each branch is structurally
# defined (captures a FORMAT, not a specific domain or journal name) so
# it generalizes to any paper/publisher without maintenance.
_LINE_NOISE_RE = re.compile(
    r"(?i)^(?:"
    r"(?:copyright\s*)?(?:\(c\)|©)\s*\d{4}"  # any copyright notice
    r"|doi:\s*10\.\d{4,}"  # any DOI
    r"|https?://\S{10,}"  # any bare URL line (no surrounding prose)
    r"|www\.\S{5,}"  # any www. bare URL line
    r"|vol\.?\s*\d+\s*[,|]\s*(?:no\.?\s*\d+|issue)"  # volume/issue metadata
    r"|pp?\.?\s*\d+\s*[-–]\s*\d+"  # page-range metadata
    r"|e?-?mail:\s*\S+"  # email: lines
    r")",
)

# Structural detector for journal date-block metadata. Instead of
# pattern-matching specific date formats, we detect short paragraphs
# that contain ≥2 of the canonical journal-lifecycle verbs AND a 4-digit
# year. These are ALWAYS metadata — no research prose contains
# "Received: Month Day, Year Accepted: Month Day, Year" as a sentence.
_DATE_BLOCK_VERBS = frozenset({"received", "accepted", "published", "revised"})


def _is_date_metadata_line(line: str) -> bool:
    """True if ``line`` is a journal date-block rather than research prose."""
    if len(line) > 250:
        return False
    lower = line.lower()
    verb_hits = sum(1 for v in _DATE_BLOCK_VERBS if v in lower)
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", line))
    return verb_hits >= 2 and has_year


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
    """Drop individual lines that match structural noise patterns.

    Only drops *short* lines — boilerplate footers are always short, and
    long lines (full paragraphs that happen to mention a noise substring
    in passing) must be preserved. Paragraph-level noise is handled
    separately by ``_strip_noise_paragraphs``.

    Three structural detectors (none hardcodes a specific journal or
    domain):
    1. Substring: licensing/access-control phrases that never appear in
       research prose.
    2. Regex: format-based patterns (copyright, DOI, bare URL, page
       range, email line, volume/issue).
    3. Date-block: short lines with ≥2 of {received, accepted, published,
       revised} + a 4-digit year — always journal metadata, never prose.
    """
    out: list[str] = []
    for ln in md.split("\n"):
        stripped = ln.strip()
        if stripped and len(stripped) <= 250:
            low = stripped.lower()
            if any(s in low for s in _LINE_NOISE_SUBSTRINGS):
                continue
            if _LINE_NOISE_RE.match(stripped):
                continue
            if _is_date_metadata_line(stripped):
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


_REFS_SPLIT_RE = re.compile(
    r"^(#{1,3})[^A-Za-z0-9\n]*(?:\d+[\d.]*\s*)?"
    r"(?:references?|bibliography|works\s+cited)"
    r"(?:\s+and\s+notes)?[^A-Za-z0-9\n]*$",
    re.IGNORECASE | re.MULTILINE,
)


def clean_markdown_text(md: str) -> str:
    """Run the full parse-time cleanup pipeline.

    Order matters:
      1. line-level noise (licensing notices) — cheap, removes lines that
         would otherwise foul the running-header detector
      2. running-header dedup — needs the licensing junk gone first
      3. leading journal heading
      4. paragraph-level noise (must run last so paragraphs are coherent)
      5. body normalization — whitespace, hyphen rejoin, replacement
         char removal, dropcap artifact repair, empty citation markers

    Splits the document at the references heading (when present) and
    only runs the aggressive paragraph-level noise filter on the body.
    A real citation line usually contains "doi:" and other tokens that
    our noise-marker list is designed to strip — without this split the
    whole references section gets erased.
    """
    if not md:
        return md
    # Split at references heading so we can protect the refs body from
    # aggressive noise filtering.
    refs_match = _REFS_SPLIT_RE.search(md)
    if refs_match:
        body = md[: refs_match.start()]
        refs_tail = md[refs_match.start() :]
    else:
        body = md
        refs_tail = ""

    body = _strip_line_noise(body)
    body = _strip_repeated_headers(body)
    body = _strip_leading_journal_heading(body)
    body = _strip_noise_paragraphs(body)
    body = _normalize_body_text(body)

    if refs_tail:
        # References get lighter-touch cleanup: line-noise trim + body
        # normalization (whitespace, hyphen rejoin, Unicode drop) but
        # NOT paragraph-level noise filtering. Running headers and
        # licensing footers have already been removed by pymupdf4llm's
        # layout engine.
        refs_tail = _strip_line_noise(refs_tail)
        refs_tail = _normalize_body_text(refs_tail)
        out = body + "\n" + refs_tail
    else:
        out = body

    # Collapse the blank-line storms the strippers leave behind.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n"


# --- body normalization --------------------------------------------------

# pymupdf4llm's column reconstruction sometimes drops a dropcap letter
# into the middle of a hyphenated word split, e.g. "conven- **D** tional"
# where the large "D" was actually the dropcap of "DUE" a few tokens
# earlier. We can't relocate the letter, but we can drop it and rejoin
# the hyphen so the word is at least readable: "conventional".
_DROPCAP_ARTIFACT_RE = re.compile(
    r"([a-z])-\s*\*\*[A-Z]\*\*\s*([a-z])",
)

# Dangling citation-marker remnants after the earlier _strip_pdf_artifacts
# pass: "applications , ." or "[a], ., [b]". Not devastating but they
# waste context and confuse downstream sentence splitters.
_EMPTY_CITE_CLUSTER_RE = re.compile(r"\s*,\s*(?:,\s*)+")
_DANGLING_COMMA_PERIOD_RE = re.compile(r"\s+,\s*\.")

# Invisible / replacement / zero-width characters that add zero
# information and inflate LLM context. Keep normal spaces and line
# breaks; drop the rest.
_NOISE_CHARS_RE = re.compile(r"[\ufffd\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _normalize_body_text(md: str) -> str:
    """Tighten chunk-level whitespace and repair common PDF artifacts.

    This runs after the structural strippers. Changes are purely
    cosmetic/size: rejoin hyphenated words across line breaks, repair
    dropcap reconstruction artifacts, drop replacement characters,
    collapse multi-space runs, and strip trailing whitespace per line.
    """
    # 1. Drop replacement / zero-width characters.
    md = _NOISE_CHARS_RE.sub("", md)
    # 2. Rejoin hyphenated words split across a newline: "conven-\ntional"
    #    → "conventional". Only rejoin when a lowercase letter is on both
    #    sides so we don't glue hyphenated compounds like "3-D CMOL".
    md = re.sub(r"([a-z])-\n([a-z])", r"\1\2", md)
    # 3. Dropcap repair: "conven- **D** tional" → "conventional" by
    #    discarding the misplaced dropcap and gluing the hyphenated halves
    #    back together. The stray letter is already in the wrong position,
    #    so dropping it is strictly better than leaving "convendtional".
    md = _DROPCAP_ARTIFACT_RE.sub(r"\1\2", md)
    # 4. Dangling citation punctuation left after bracket-marker removal.
    md = _EMPTY_CITE_CLUSTER_RE.sub(", ", md)
    md = _DANGLING_COMMA_PERIOD_RE.sub(".", md)
    # 5. Per-line trailing whitespace: very common in pymupdf4llm output
    #    and adds tokens for no signal.
    md = re.sub(r"[ \t]+\n", "\n", md)
    # 6. Collapse runs of spaces (but never newlines) to a single space.
    md = re.sub(r"[ \t]{2,}", " ", md)
    # 7. Normalize the IEEE-style "Index Terms" section label to "Keywords"
    #    — the two are identical in purpose and we'd rather report one
    #    canonical name across the corpus (topics.py already accepts both).
    #    The source can wrap the label in any combination of ``_``/``*``
    #    emphasis markers (``## _**Index Terms- ...**_``), so we match the
    #    whole line and strip both the opener and closer in one shot.
    md = re.sub(
        r"(?im)^(#+\s*)[_*\s]*index\s+terms[\s\-—:.,_*]*(.+?)[_*\s]*$",
        r"\1**Keywords:** \2",
        md,
    )
    return md
