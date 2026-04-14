"""Standalone heuristic citation text parser.

Extracts structured metadata (title, authors, venue, volume, pages)
from raw academic citation strings.  Zero external dependencies beyond
stdlib -- can be applied to any text independently of wikify.

Supports all major citation styles: IEEE, Nature/Science, ACS, APA,
Vancouver, Chicago, MLA, Harvard, Elsevier, RSC, AIP/APS.

Also provides cross-paper evidence fusion for combining metadata from
multiple citation strings referencing the same work.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Vocabulary (self-contained, no external imports)
# ---------------------------------------------------------------------------

_JOURNAL_ABBREV_TOKENS = {
    "adv", "appl", "chem", "commun", "electron", "eng", "funct", "lett",
    "mater", "nanotechnol", "phys", "rev", "sci", "technol", "trans",
    "proc", "int", "conf", "symp", "syst", "comput", "biol", "med",
    "opt", "solid", "surf", "thin", "vac", "semicond", "supercond",
    "magn", "microelectron", "nanolett", "acta", "annu",
}

_JOURNAL_FULL_WORDS = {
    "journal", "proceedings", "transactions", "letters", "review", "reviews",
    "annals", "bulletin", "reports", "communications", "magazine", "quarterly",
    "archives", "nano", "nature", "science", "cell",
}

_AUTHOR_NOISE = {
    "ieee", "member", "senior", "fellow", "student", "life", "associate",
    "et", "al", "and", "the", "of", "vol", "no",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "transactions", "journal", "proceedings", "letters",
}


def _clean_doi(raw: str) -> str:
    """Validate and clean a raw DOI string."""
    doi = re.sub(r"\s+", "", raw)  # collapse any whitespace
    doi = doi.rstrip(".,;)]}>")
    # Must have a suffix after the registrant prefix (10.XXXX/)
    # Reject truncated DOIs: "10.1016/j", "10.1109/LED", "10.1109/LED.2017"
    m = re.match(r"^10\.\d{4,}/(.+)$", doi)
    if not m:
        return ""
    suffix = m.group(1)
    # Minimum suffix length
    if len(suffix) < 5:
        return ""
    # Suffix must contain a digit (rejects pure alpha like "aelm", "annurev")
    if not re.search(r"\d", suffix):
        return ""
    # Reject "ABBREV.YEAR" pattern (truncated IEEE DOIs like "LED.2017")
    if re.match(r"^[A-Z]{2,6}\.\d{4}$", suffix):
        return ""
    # Reject journal-prefix-only: suffix is all alpha (no digits past the slash)
    if re.match(r"^[a-z]+$", suffix, re.I):
        return ""
    return doi


def extract_doi(text: str) -> str:
    """Extract a DOI from text, including from URLs with spaces.

    Handles:
    - Bare DOIs: ``10.1038/s41563-019-0291-x``
    - Standard URLs: ``https://doi.org/10.1038/s41563-019-0291-x``
    - URLs with spaces: ``https://doi.org/10.1002/ aelm.201900287``
    - DOI prefix: ``doi: 10.1038/...``
    """
    # First try: extract from doi.org URL (handles spaces in URL path)
    m = re.search(
        r"(?:https?://)?doi\.org/\s*(10\.\d{4,}[^\s,;)\]]*(?:\s[^\s,;)\]]+)*)",
        text,
    )
    if m:
        return _clean_doi(m.group(1))
    # Second try: bare DOI pattern (greedy, then validate)
    m = re.search(r"\b(10\.\d{4,}/[^\s,;)\]]+)", text)
    if m:
        return _clean_doi(m.group(1))
    # Third try: "doi:" prefix
    m = re.search(r"doi:\s*(10\.\d{4,}/[^\s,;)\]]+)", text, re.I)
    if m:
        return _clean_doi(m.group(1))
    return ""


def looks_like_journal(name: str) -> bool:
    """Return True if *name* looks like a journal title."""
    tokens = [w.lower().rstrip(".,;:") for w in name.split()]
    abbrev_hits = sum(1 for t in tokens if t in _JOURNAL_ABBREV_TOKENS)
    if abbrev_hits >= 2:
        return True
    token_set = set(tokens)
    journal_hits = token_set & _JOURNAL_FULL_WORDS
    if journal_hits:
        if not (token_set - _JOURNAL_FULL_WORDS - _JOURNAL_ABBREV_TOKENS):
            return True
        if len(tokens) <= 2:
            return True
    if abbrev_hits >= 1 and len(tokens) <= 2:
        non_abbrev = [t for t in tokens if t not in _JOURNAL_ABBREV_TOKENS]
        if all(len(t) <= 4 or t.upper() == t for t in non_abbrev):
            return True
    return False


def is_valid_author(name: str) -> bool:
    """Return True if *name* looks like a person's name."""
    name = name.strip()
    if not name or len(name) < 2:
        return False
    words = name.split()
    if len(words) == 1 and not any(
        "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7af" for c in name
    ):
        return False
    if len(words) > 5:
        return False
    if not words[0][0:1].isupper():
        return False
    if re.search(r"[(\[|]|\d+\s*$", name):
        return False
    if all(w.lower() in _AUTHOR_NOISE for w in words):
        return False
    if looks_like_journal(name):
        return False
    if "et al" in name.lower():
        return False
    if "&" in name:
        return False
    stripped = re.sub(r"[.\-\s]", "", name)
    if stripped.isupper() and len(stripped) <= 4:
        return False
    if all(len(w.rstrip(".")) <= 2 for w in words):
        return False
    if re.search(r"\b(19|20)\d{2}\b", name):
        return False
    return True


def parse_authors(raw: str) -> list[str]:
    """Parse an author string into a list of individual names."""
    raw = raw.replace(";", ",").replace(" and ", ",").replace(" & ", ",")
    parts = [a.strip() for a in raw.split(",") if a.strip()]
    assembled: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if i + 1 < len(parts):
            nxt = parts[i + 1].strip()
            is_initials = bool(re.match(r"^[A-Z][.\s]*(?:[A-Z]\.?\s*)*$", nxt))
            is_first_name = bool(
                re.match(r"^[A-Z][a-z]{1,14}$", nxt)
                and len(part.split()) == 1
                and part[0:1].isupper()
            )
            if is_initials or is_first_name:
                assembled.append(f"{nxt} {part}")
                i += 2
                continue
        assembled.append(part)
        i += 1
    return [a for a in assembled if is_valid_author(a)]


# ---------------------------------------------------------------------------
# Format detection — discrimination tree
# ---------------------------------------------------------------------------
#
# Strategy groups (not 1:1 with styles, grouped by extraction pattern):
#   QUOTED   — title in quotes: IEEE, Chicago, MLA, Harvard(single)
#   APA      — year in parens after authors: APA, Harvard
#   ACS      — semicolons between authors, then title sentence
#   PERIODED — title is a sentence between author block and venue:
#              Nature, Vancouver, Elsevier, AIP/APS, RSC

_FMT_QUOTED = "quoted"
_FMT_APA = "apa"
_FMT_ACS = "acs"
_FMT_ELSEVIER = "elsevier"
_FMT_PERIODED = "perioded"

# Quoted title: double quotes, smart quotes, guillemets, mojibake
_QUOTED_TITLE_RE = re.compile(
    r'[\u00ab\u201c"\u00e2\u0093]'
    r'((?:[^\u00bb\u201d"\u00e2\u0094]){10,})'
    r'[\u00bb\u201d"\u00e2\u0094,]'
)
# Single-quoted title (Harvard)
_SINGLE_QUOTED_RE = re.compile(r"'([^']{10,})'")
# APA: year in parens after author block
_APA_YEAR_RE = re.compile(r"\(\d{4}[a-z]?\)\.\s+")
# ACS: semicolons between authors (not year;vol)
_ACS_SEMICOL_RE = re.compile(r"^[^;]{5,50};[^;]{5,50};")


def detect_format(raw: str) -> str:
    """Classify a citation string into an extraction strategy group."""
    # Quoted title is the strongest signal
    if _QUOTED_TITLE_RE.search(raw) or _SINGLE_QUOTED_RE.search(raw):
        return _FMT_QUOTED
    # APA: year in parens early in string, followed by period
    if _APA_YEAR_RE.search(raw[:int(len(raw) * 0.5)]):
        return _FMT_APA
    # ACS: semicolons between authors (but not year;vol Vancouver pattern)
    first_80 = raw[:80]
    if ";" in first_80 and not re.search(r"\d{4};", first_80):
        return _FMT_ACS
    # Elsevier numbered: comma-delimited, no quotes, no vol./pp.
    # Key distinguisher from Nature: no periods between title and journal
    # (comma-separated throughout), and author initials lack periods
    # (e.g., "J.J. Yang" not "J. J. Yang").
    # Detect by: "- Authors, Title, J. Abbrev. Vol (Year) Pages, DOI."
    # where there are NO ". " boundaries between authors and journal.
    if (not re.search(r"\bvol\.\b|\bpp\.\b", raw, re.I)
        and "&" not in raw[:80]
        and re.search(r"https?://doi\.org/|doi\.org/", raw)
        and not re.search(r"\.\s+[A-Z][a-z]{3,}", raw[:int(len(raw) * 0.4)])):
        return _FMT_ELSEVIER
    # Default: period-delimited (Nature, Vancouver, etc.)
    return _FMT_PERIODED


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

def _extract_title_quoted(raw: str) -> str:
    """Extract title from quoted text (IEEE, Chicago, MLA, Harvard)."""
    m = _QUOTED_TITLE_RE.search(raw)
    if not m:
        m = _SINGLE_QUOTED_RE.search(raw)
    if m:
        return m.group(1).strip().rstrip(",.")
    return ""


def _extract_title_apa(raw: str) -> str:
    """Extract title after '(YYYY). ' (APA, Harvard variant)."""
    m = _APA_YEAR_RE.search(raw)
    if not m:
        return ""
    rest = raw[m.end():]
    # Title runs until period followed by space and uppercase (venue start)
    m2 = re.search(r"\.\s+(?=[A-Z])", rest)
    if m2:
        return rest[:m2.start()].strip().rstrip(".")
    return rest.strip().rstrip(".")


def _extract_title_acs(raw: str) -> str:
    """Extract title from ACS-style: authors; authors; ... Title. Journal."""
    # Find the last semicolon in the author block
    last_semi = raw.rfind(";")
    if last_semi < 0:
        return ""
    # After the last semicolon, find the first period-space boundary
    rest = raw[last_semi + 1:]
    # Skip the last author name (text up to first ". ")
    m = re.search(r"\.\s+", rest)
    if not m:
        return ""
    title_start = last_semi + 1 + m.end()
    title_rest = raw[title_start:]
    # Title ends at next period followed by venue-like text
    return _find_title_end(title_rest)


def _extract_title_elsevier(raw: str) -> str:
    """Extract title from Elsevier comma-delimited style.

    Format: ``- Authors, Title, J. Abbrev. Vol (Year) Pages, DOI.``
    All fields are comma-separated. The title is the longest comma-segment
    that looks like a sentence (starts uppercase, has lowercase words).
    """
    # Strip leading dash/number markers
    clean = re.sub(r"^[\s\-\[\d.\)]+", "", raw)
    # Remove trailing DOI URL
    clean = re.sub(r",?\s*https?://\S+\.?\s*$", "", clean)
    # Split on commas
    segments = [s.strip() for s in clean.split(",") if s.strip()]
    # The title is typically the longest segment that:
    # - starts with uppercase
    # - has >= 4 words
    # - contains lowercase words (not all-caps or initials)
    best_title = ""
    for seg in segments:
        words = seg.split()
        if (len(words) >= 4
            and seg[0].isupper()
            and any(w[0].islower() for w in words[1:] if len(w) > 2)
            and not looks_like_journal(seg)
            and len(seg) > len(best_title)):
            best_title = seg
    return best_title.rstrip(".")


def _looks_like_author_context(text: str) -> bool:
    """Return True if text looks like it's inside an author list."""
    snippet = text[:60]
    initials = len(re.findall(r"\b[A-Z]\.", snippet))
    commas = snippet.count(",")
    return initials >= 2 and commas >= 2


def _extract_title_perioded(raw: str) -> str:
    """Extract title from period-delimited styles (Nature, Vancouver, etc.).

    The title is the first multi-word sentence after the author block,
    ending before a venue-like token.
    """
    candidates: list[int] = []
    for m in re.finditer(r"\.\s+", raw):
        rest = raw[m.end():]
        if not rest:
            continue
        # Skip single-letter initials
        if re.match(r"^[A-Z]\.\s", rest):
            continue
        # Skip author separators
        if rest[0] in "&,":
            continue
        # Skip "et al."
        if rest.startswith("et al"):
            continue
        # Skip if still inside author list
        if _looks_like_author_context(rest):
            continue
        words = rest.split(None, 5)
        if len(words) >= 3 and any(
            len(w) > 3 and w[0].isupper() and not w.isupper()
            for w in words[:3]
        ):
            candidates.append(m.end())

    if not candidates:
        return ""

    rest = raw[candidates[0]:]
    rest = re.sub(r"^et\s+al\.?\s*", "", rest)
    return _find_title_end(rest)


def _find_title_end(text: str) -> str:
    """Find where a title ends — at the period before a venue-like token."""
    best_end = len(text)
    for m in re.finditer(r"\.\s+", text):
        after = text[m.end():]
        if (re.match(r"[A-Z][a-z]*\.\s", after)  # abbreviated journal
            or re.match(r"[A-Z][a-z]+\s+\d", after)  # "Nature 433"
            or re.match(r"[A-Z][A-Z]", after)  # acronym "IEEE"
            or re.match(r"\d+\s*[,(]", after)  # bare volume
            or looks_like_journal(after.split(".")[0].strip())):
            best_end = m.start()
            break

    title = text[:best_end].strip().rstrip(".")
    if len(title) < 15 or len(title.split()) < 3:
        return ""
    return title


_TITLE_EXTRACTORS = {
    _FMT_QUOTED: _extract_title_quoted,
    _FMT_APA: _extract_title_apa,
    _FMT_ACS: _extract_title_acs,
    _FMT_ELSEVIER: _extract_title_elsevier,
    _FMT_PERIODED: _extract_title_perioded,
}


def extract_title(raw: str, fmt: str | None = None) -> str:
    """Extract a probable title from a raw citation string."""
    if fmt is None:
        fmt = detect_format(raw)
    extractor = _TITLE_EXTRACTORS.get(fmt, _extract_title_perioded)
    return extractor(raw)


# ---------------------------------------------------------------------------
# Author extraction
# ---------------------------------------------------------------------------

def extract_authors(raw: str, title: str, fmt: str | None = None) -> list[str]:
    """Extract full author names from text preceding the title."""
    if not title:
        return []
    title_pos = raw.find(title[:30]) if len(title) >= 30 else raw.find(title)
    if title_pos <= 0:
        return []
    block = raw[:title_pos]
    # Clean leading list markers, trailing punctuation
    block = re.sub(r"^[\s\-\[\d.\)]+", "", block)
    block = block.rstrip(" .,;:&")
    block = re.sub(r"\bet\s+al\.?\s*", "", block)
    if len(block) < 3:
        return []
    return parse_authors(block)


# ---------------------------------------------------------------------------
# Venue / volume / pages
# ---------------------------------------------------------------------------

_VOLUME_RE = re.compile(r"(?:vol\.?\s*)?(\d{1,4})\s*[,(]")
_PAGES_RE = re.compile(r"(?:pp?\.?\s*)?(\d+)\s*[-\u2013]\s*(\d+)")
_ARTICLE_NUM_RE = re.compile(r"\b(\d{5,7})\b")


def extract_venue_fields(raw: str, title: str) -> dict[str, str]:
    """Extract venue, volume, and pages from text after the title."""
    result: dict[str, str] = {}
    if not title:
        return result
    title_fragment = title[:30] if len(title) >= 30 else title
    pos = raw.find(title_fragment)
    if pos < 0:
        return result
    after = raw[pos + len(title):].lstrip(
        ' .,;:"\u00ab\u00bb\u201c\u201d\u0093\u0094'
    )

    vm = _VOLUME_RE.search(after)
    if vm:
        result["volume"] = vm.group(1)

    pm = _PAGES_RE.search(after)
    if pm:
        result["pages"] = f"{pm.group(1)}--{pm.group(2)}"
    else:
        am = _ARTICLE_NUM_RE.search(after)
        if am:
            result["pages"] = am.group(1)

    venue_end = len(after)
    for m in re.finditer(r"\d", after):
        venue_end = m.start()
        break
    venue = after[:venue_end].strip().rstrip(" .,;:")
    venue = re.sub(r"^\s*[,;:]\s*", "", venue)
    venue = venue.lstrip(' "\u00ab\u00bb\u201c\u201d\u0093\u0094')
    if venue and len(venue) >= 3 and not venue.isdigit():
        result["venue"] = venue

    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_BIB_NOISE = {"vol.", "pp.", "no.", "doi:", "issn", "isbn"}


def _is_valid_title(title: str) -> bool:
    """Reject garbage titles."""
    if len(title) < 15 or len(title.split()) < 3:
        return False
    if looks_like_journal(title):
        return False
    if title[0].islower():
        return False
    tl = title.lower()
    if any(marker in tl for marker in _BIB_NOISE):
        return False
    if title.count(",") > len(title.split()) // 2:
        return False
    # Reject raw citation strings masquerading as titles:
    # they have author-initial patterns like "S.Y." or "K.-H."
    initials = len(re.findall(r"\b[A-Z]\.\s*[A-Z]?\.", title))
    if initials >= 3:
        return False
    # Reject if it starts with "Author, Initials" pattern
    if re.match(r"^[A-Z][a-z]+,\s*[A-Z]", title):
        return False
    # Reject if it starts with "- " (reference list marker leaked)
    if title.startswith("- ") or title.startswith("> "):
        return False
    # Reject if it contains semicolons (ACS author-list leaked as title)
    if title.count(";") >= 2:
        return False
    # Reject if it looks like "Author, Initials, Author, Initials, ..."
    # (comma-delimited author list with initials)
    if re.match(r"^[A-Z][\w.-]+,\s*[A-Z][\w.]+,\s*[A-Z]", title):
        return False
    # Reject "Name, and Name, Journal" pattern (author block leaked)
    if re.match(r"^[A-Z][a-z]+,?\s+and\s+", title):
        return False
    # Reject all-caps short titles ("CASCADE", "EDITED BY")
    if title.isupper() and len(title.split()) <= 3:
        return False
    # Reject titles containing URLs
    if "https://" in title or "doi.org" in title:
        return False
    return True


def _is_valid_venue(venue: str) -> bool:
    """Reject garbage venue strings."""
    if len(venue) < 3 or len(venue) > 60:
        return False
    initials = len(re.findall(r"\b[A-Z]\.", venue))
    if initials >= 2:
        return False
    if venue[0].islower():
        return False
    return True


# ---------------------------------------------------------------------------
# Single-citation parse (main entry point for standalone use)
# ---------------------------------------------------------------------------

def parse_citation(raw: str, *, year: int | None = None) -> dict[str, object]:
    """Parse a raw citation string into structured metadata.

    Returns a dict with keys: title, authors, venue, volume, pages
    (each may be empty/missing if extraction fails).  This is the
    main standalone entry point -- no wikify dependencies needed.
    """
    if not raw or len(raw) < 25:
        return {}

    fmt = detect_format(raw)
    result: dict[str, object] = {}

    # Extract DOI from URLs and bare patterns
    doi = extract_doi(raw)
    if doi:
        result["doi"] = doi

    title = extract_title(raw, fmt)
    if title and _is_valid_title(title):
        result["title"] = title

    title = result.get("title", "")
    if title:
        authors = extract_authors(raw, title, fmt)
        if authors:
            result["authors"] = authors
        fields = extract_venue_fields(raw, title)
        venue = fields.get("venue", "")
        if venue and _is_valid_venue(venue):
            result["venue"] = venue
        for k in ("volume", "pages"):
            if fields.get(k):
                result[k] = fields[k]

    return result


# ---------------------------------------------------------------------------
# Cross-paper evidence fusion
# ---------------------------------------------------------------------------

def citation_fingerprint(cit: dict) -> str:
    """Dedup key from DOI or (first 3 author last names + year)."""
    doi = cit.get("doi", "")
    if doi:
        return f"doi:{doi.lower().strip()}"
    names = sorted(n.lower() for n in cit.get("author_last_names", [])[:3])
    year = cit.get("year")
    if names and year:
        return f"auth:{','.join(names)}:{year}"
    return ""


def _fuse_field(values: list, field: str) -> object:
    if not values:
        return None
    if field in ("title", "authors"):
        return max(values, key=lambda v: len(v) if isinstance(v, (str, list)) else 0)
    if field == "doi":
        return values[0]
    if field == "year":
        return Counter(values).most_common(1)[0][0]
    hashable = [tuple(v) if isinstance(v, list) else v for v in values]
    return Counter(hashable).most_common(1)[0][0]


def _validate_fused_value(field: str, value: object) -> bool:
    """Validate a fused value before propagating it."""
    if not value:
        return False
    if field == "title":
        return isinstance(value, str) and _is_valid_title(value)
    if field == "authors":
        return isinstance(value, list) and len(value) > 0
    if field == "venue":
        return isinstance(value, str) and _is_valid_venue(value)
    return True


def fuse_cross_paper_evidence(all_citations: list[list[dict]]) -> None:
    """Merge evidence across multiple citation lists for the same works."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for doc_cits in all_citations:
        for cit in doc_cits:
            fp = citation_fingerprint(cit)
            if fp:
                buckets[fp].append(cit)

    for _fp, group in buckets.items():
        if len(group) < 2:
            continue
        merged: dict[str, object] = {}
        for field in ("title", "authors", "venue", "volume", "pages", "doi", "year"):
            values = [c.get(field) for c in group if c.get(field)]
            if values:
                best = _fuse_field(values, field)
                if _validate_fused_value(field, best):
                    merged[field] = best
        for cit in group:
            for key, value in merged.items():
                if value and not cit.get(key):
                    cit[key] = value
