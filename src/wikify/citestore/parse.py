"""Standalone heuristic citation text parser.

Extracts structured metadata (title, authors, venue, volume, pages)
from raw academic citation strings.  Zero external dependencies beyond
stdlib -- can be applied to any text independently of wikify.

Also provides cross-paper evidence fusion for combining metadata from
multiple citation strings referencing the same work.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Vocabulary (self-contained, no wikify imports)
# ---------------------------------------------------------------------------

_JOURNAL_ABBREV_TOKENS = {
    "adv", "appl", "chem", "commun", "electron", "eng", "funct", "lett",
    "mater", "nanotechnol", "phys", "rev", "sci", "technol", "trans",
    "proc", "int", "conf", "symp",
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
    return True


def parse_authors(raw: str) -> list[str]:
    """Parse an author string into a list of individual names.

    Handles "Last, Initials" and "Initials Last" formats, including
    comma-separated lists with initial reassembly.
    """
    raw = raw.replace(";", ",").replace(" and ", ",")
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
# Format detection
# ---------------------------------------------------------------------------

_FMT_IEEE = "ieee"
_FMT_NATURE = "nature"
_FMT_APA = "apa"

_QUOTED_TITLE_RE = re.compile(
    r'[\u00ab\u201c"\u00e2\u0093]'
    r'((?:[^\u00bb\u201d"\u00e2\u0094]){10,})'
    r'[\u00bb\u201d"\u00e2\u0094,]'
)
_APA_YEAR_RE = re.compile(r"\(\d{4}[a-z]?\)\.\s+")


def detect_format(raw: str) -> str:
    """Classify a citation string as ieee, nature, or apa."""
    if _QUOTED_TITLE_RE.search(raw):
        return _FMT_IEEE
    if _APA_YEAR_RE.search(raw[:int(len(raw) * 0.6)]):
        return _FMT_APA
    return _FMT_NATURE


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

def _extract_title_ieee(raw: str) -> str:
    m = _QUOTED_TITLE_RE.search(raw)
    if m:
        return m.group(1).strip().rstrip(",.")
    return ""


def _extract_title_apa(raw: str) -> str:
    m = _APA_YEAR_RE.search(raw)
    if not m:
        return ""
    rest = raw[m.end():]
    m2 = re.search(r"\.\s+(?=[A-Z])", rest)
    if m2:
        return rest[:m2.start()].strip().rstrip(".")
    return rest.strip().rstrip(".")


def _extract_title_nature(raw: str) -> str:
    candidates: list[int] = []
    for m in re.finditer(r"\.\s+", raw):
        rest = raw[m.end():]
        if not rest:
            continue
        if re.match(r"^[A-Z]\.\s", rest):
            continue
        if rest[0] == "&":
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
    best_end = len(rest)
    for m in re.finditer(r"\.\s+", rest):
        after = rest[m.end():]
        if (re.match(r"[A-Z][a-z]*\.\s", after)
            or re.match(r"[A-Z][a-z]+\s+\d", after)
            or re.match(r"[A-Z][A-Z]", after)
            or re.match(r"\d+\s*[,(]", after)
            or looks_like_journal(after.split(".")[0].strip())):
            best_end = m.start()
            break

    title = rest[:best_end].strip().rstrip(".")
    if len(title) < 10 or len(title.split()) < 3:
        return ""
    return title


def extract_title(raw: str, fmt: str | None = None) -> str:
    """Extract a probable title from a raw citation string."""
    if fmt is None:
        fmt = detect_format(raw)
    if fmt == _FMT_IEEE:
        return _extract_title_ieee(raw)
    if fmt == _FMT_APA:
        return _extract_title_apa(raw)
    return _extract_title_nature(raw)


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
    after = raw[pos + len(title):].lstrip(" .,;:")

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
    if venue and len(venue) >= 3 and not venue.isdigit():
        result["venue"] = venue

    return result


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

    title = extract_title(raw, fmt)
    if title and len(title) >= 10 and len(title.split()) >= 3:
        if not looks_like_journal(title):
            result["title"] = title

    title = result.get("title", "")
    if title:
        authors = extract_authors(raw, title, fmt)
        if authors:
            result["authors"] = authors
        result.update(extract_venue_fields(raw, title))

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
                merged[field] = _fuse_field(values, field)
        for cit in group:
            for key, value in merged.items():
                if value and not cit.get(key):
                    cit[key] = value
