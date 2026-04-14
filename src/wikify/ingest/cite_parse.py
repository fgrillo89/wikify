"""Heuristic citation metadata extraction from raw reference text.

Enriches citation dicts produced by ``citations.extract_citations()``
with best-effort structured fields: title, authors, venue, volume,
pages.  Three enrichment layers, each additive:

1. Regex heuristics  (zero API calls)
2. DOI content negotiation  (free, unlimited, no API key)
3. Cross-paper evidence fusion  (across the whole corpus)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING

from .metadata import _looks_like_journal, parse_authors

if TYPE_CHECKING:
    from ..models import Document

# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_FMT_IEEE = "ieee"
_FMT_NATURE = "nature"  # Nature/Science/ACS: "Author, A. B. Title. Journal Vol, Pages (Year)."
_FMT_APA = "apa"

# IEEE: quoted title with guillemets, smart quotes, or mojibake
_QUOTED_TITLE_RE = re.compile(
    r'[\u00ab\u201c"\u00e2\u0093]'  # open: « " " or mojibake variants
    r'((?:[^\u00bb\u201d"\u00e2\u0094]){10,})'  # content
    r'[\u00bb\u201d"\u00e2\u0094,]'  # close: » " " or comma
)
# APA: year in parentheses after author block
_APA_YEAR_RE = re.compile(r"\(\d{4}[a-z]?\)\.\s+")
# Nature: "Title. Journal Vol, Pages (Year)." -- year in parens at end
_NATURE_YEAR_RE = re.compile(r"\((\d{4})\)\s*\.?\s*$")


def _detect_format(raw: str) -> str:
    if _QUOTED_TITLE_RE.search(raw):
        return _FMT_IEEE
    if _APA_YEAR_RE.search(raw[:int(len(raw) * 0.6)]):
        return _FMT_APA
    return _FMT_NATURE  # catch-all


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

def _extract_title_ieee(raw: str) -> str:
    """IEEE: title is inside quotes or guillemets."""
    m = _QUOTED_TITLE_RE.search(raw)
    if m:
        return m.group(1).strip().rstrip(",.")
    return ""


def _extract_title_apa(raw: str) -> str:
    """APA: title follows '(YYYY). ' up to the next sentence-ending period."""
    m = _APA_YEAR_RE.search(raw)
    if not m:
        return ""
    start = m.end()
    rest = raw[start:]
    # Title runs until period followed by space and a capitalized venue-like word
    # or an italic marker, or end of string
    m2 = re.search(r"\.\s+(?=[A-Z])", rest)
    if m2:
        return rest[:m2.start()].strip().rstrip(".")
    return rest.strip().rstrip(".")


def _extract_title_nature(raw: str, year: int | None) -> str:
    """Nature/Science style: authors. Title. Journal Vol, Pages (Year).

    Strategy: find the author-title boundary (first sentence-boundary
    after author block), then find the title-venue boundary (the period
    before a venue-shaped token).
    """
    # The Nature format has authors delimited by periods after initials,
    # making naive period-splitting unreliable. Instead, use the year
    # to anchor: everything between the author block and the venue block.
    #
    # Heuristic: the title starts at the FIRST period+space that is
    # followed by a long capitalized phrase (>= 3 words with lowercase).
    # The title ENDS at the next period+space followed by a short
    # venue-like token (abbreviated journal, or italic marker).

    # Step 1: find candidate title start positions
    # After author names we expect ". Title..." — look for ". " followed
    # by a word that is NOT a single-letter initial (len > 2, mixed case)
    candidates: list[int] = []
    for m in re.finditer(r"\.\s+", raw):
        rest = raw[m.end():]
        if not rest:
            continue
        # Skip single-letter initials ("A. ", "B. ")
        if re.match(r"^[A-Z]\.\s", rest):
            continue
        # Skip "&" (author separator)
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

    title_start = candidates[0]
    rest = raw[title_start:]

    # Step 2: find title end — the period before a venue-like token
    # Venue indicators: abbreviated journal names (with periods), volume numbers
    best_end = len(rest)
    for m in re.finditer(r"\.\s+", rest):
        after = rest[m.end():]
        # Check if what follows looks like a journal name or volume
        if (re.match(r"[A-Z][a-z]*\.\s", after)  # abbreviated: "Adv. "
            or re.match(r"[A-Z][a-z]+\s+\d", after)  # "Nature 433"
            or re.match(r"[A-Z][A-Z]", after)  # acronym: "IEEE"
            or re.match(r"\d+\s*[,(]", after)  # bare volume number
            or _looks_like_journal(after.split(".")[0].strip())):
            best_end = m.start()
            break

    title = rest[:best_end].strip().rstrip(".")
    # Validate
    if len(title) < 10 or len(title.split()) < 3:
        return ""
    return title


def _extract_title(raw: str, fmt: str, year: int | None) -> str:
    if fmt == _FMT_IEEE:
        return _extract_title_ieee(raw)
    if fmt == _FMT_APA:
        return _extract_title_apa(raw)
    return _extract_title_nature(raw, year)


# ---------------------------------------------------------------------------
# Author extraction
# ---------------------------------------------------------------------------

def _extract_authors_block(raw: str, fmt: str, title: str) -> str:
    """Return the raw author block text (everything before the title)."""
    if not title:
        return ""
    # Find where the title starts in the raw text
    title_pos = raw.find(title[:30]) if len(title) >= 30 else raw.find(title)
    if title_pos <= 0:
        return ""
    return raw[:title_pos]


def _extract_full_authors(raw: str, fmt: str, title: str) -> list[str]:
    """Extract full author names from the author block."""
    block = _extract_authors_block(raw, fmt, title)
    if not block:
        return []
    # Clean: strip leading list markers, trailing punctuation
    block = re.sub(r"^[\s\-\[\d.\)]+", "", block)
    block = block.rstrip(" .,;:&")
    # Remove "et al." fragments
    block = re.sub(r"\bet\s+al\.?\s*", "", block)
    if len(block) < 3:
        return []
    return parse_authors(block)


# ---------------------------------------------------------------------------
# Venue / volume / pages extraction
# ---------------------------------------------------------------------------

_VOLUME_RE = re.compile(r"(?:vol\.?\s*)?(\d{1,4})\s*[,(]")
_PAGES_RE = re.compile(r"(?:pp?\.?\s*)?(\d+)\s*[-\u2013]\s*(\d+)")
_ARTICLE_NUM_RE = re.compile(r"\b(\d{5,7})\b")  # e.g. "133515" article number


def _extract_venue_fields(raw: str, title: str) -> dict[str, str]:
    """Extract venue, volume, and pages from text after the title."""
    result: dict[str, str] = {}
    if not title:
        return result

    # Find where venue info starts (after the title)
    title_fragment = title[:30] if len(title) >= 30 else title
    pos = raw.find(title_fragment)
    if pos < 0:
        return result
    after_title = raw[pos + len(title):]
    # Strip leading punctuation
    after_title = after_title.lstrip(" .,;:")

    # Volume
    vm = _VOLUME_RE.search(after_title)
    if vm:
        result["volume"] = vm.group(1)

    # Pages
    pm = _PAGES_RE.search(after_title)
    if pm:
        result["pages"] = f"{pm.group(1)}--{pm.group(2)}"
    elif not pm:
        # Try article number (common in ACS/AIP journals)
        am = _ARTICLE_NUM_RE.search(after_title)
        if am:
            result["pages"] = am.group(1)

    # Venue: text between title end and volume/pages/year
    # Take text up to the first number-heavy token
    venue_end = len(after_title)
    for m in re.finditer(r"\d", after_title):
        venue_end = m.start()
        break
    venue = after_title[:venue_end].strip().rstrip(" .,;:")
    # Clean common artifacts
    venue = re.sub(r"^\s*[,;:]\s*", "", venue)
    if venue and len(venue) >= 3 and not venue.isdigit():
        result["venue"] = venue

    return result


# ---------------------------------------------------------------------------
# DOI content negotiation enrichment
# ---------------------------------------------------------------------------

def _default_doi_lookup(doi: str) -> dict[str, object]:
    from .bibtex import resolve_doi_metadata
    return resolve_doi_metadata(doi)


def enrich_with_doi(
    cit: dict,
    *,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> None:
    """If citation has a DOI, fetch metadata via content negotiation.

    Free, no API key, no rate limit.  Authoritative fields overwrite
    heuristic ones.
    """
    doi = cit.get("doi")
    if not doi:
        return
    lookup = doi_lookup or _default_doi_lookup
    meta = lookup(doi)
    if not meta:
        return
    # Map bibtex.resolve_doi_metadata field names to our standard names
    field_map = {
        "title": "title",
        "authors": "authors",
        "journal": "venue",
        "venue": "venue",
        "volume": "volume",
        "pages": "pages",
        "publisher": "publisher",
    }
    for src_key, dst_key in field_map.items():
        val = meta.get(src_key)
        if val:
            cit[dst_key] = val
    cit["doi_resolved"] = True


# ---------------------------------------------------------------------------
# Cross-paper evidence fusion
# ---------------------------------------------------------------------------

def _citation_fingerprint(cit: dict) -> str:
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
    """Pick the best value for a field from a group of same-work citations."""
    if not values:
        return None
    if field in ("title", "authors"):
        # Prefer longest (most complete)
        return max(values, key=lambda v: len(v) if isinstance(v, (str, list)) else 0)
    if field == "doi":
        return values[0]
    if field == "year":
        return Counter(values).most_common(1)[0][0]
    # venue / volume / pages: most common
    hashable = [tuple(v) if isinstance(v, list) else v for v in values]
    return Counter(hashable).most_common(1)[0][0]


def fuse_cross_paper_evidence(all_citations: list[list[dict]]) -> None:
    """Merge evidence across papers citing the same work, mutate in-place."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for doc_cits in all_citations:
        for cit in doc_cits:
            fp = _citation_fingerprint(cit)
            if fp:
                buckets[fp].append(cit)

    for _fp, group in buckets.items():
        if len(group) < 2:
            continue
        # Build merged record
        merged: dict[str, object] = {}
        for field in ("title", "authors", "venue", "volume", "pages", "doi", "year"):
            values = [c.get(field) for c in group if c.get(field)]
            if values:
                merged[field] = _fuse_field(values, field)
        # Write back: fill empty fields only
        for cit in group:
            for key, value in merged.items():
                if value and not cit.get(key):
                    cit[key] = value


# ---------------------------------------------------------------------------
# Single-citation heuristic parse
# ---------------------------------------------------------------------------

def parse_citation_heuristic(cit: dict) -> None:
    """Add heuristic title/authors/venue to a citation dict.  Mutates in-place.

    Only fills fields that are not already populated.
    """
    raw = cit.get("raw_text", "")
    if not raw or len(raw) < 25:
        return

    fmt = _detect_format(raw)
    year = cit.get("year")

    # Title
    if not cit.get("title"):
        title = _extract_title(raw, fmt, year)
        if title and len(title) >= 10 and len(title.split()) >= 3:
            # Reject if it looks like a journal name
            if not _looks_like_journal(title):
                cit["title"] = title

    # Authors
    if not cit.get("authors"):
        title = cit.get("title", "")
        authors = _extract_full_authors(raw, fmt, title)
        if authors:
            cit["authors"] = authors

    # Venue / volume / pages
    title = cit.get("title", "")
    if title:
        fields = _extract_venue_fields(raw, title)
        for k, v in fields.items():
            if v and not cit.get(k):
                cit[k] = v


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def enrich_citations(
    docs: list[Document],
    *,
    use_doi: bool = True,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> None:
    """Enrich all citations across all documents in-place.

    Three passes:
    1. Heuristic extraction on each citation
    2. DOI content negotiation (free, overwrites heuristics with authoritative data)
    3. Cross-paper evidence fusion (combine evidence from multiple papers)
    """
    # Pass 1: heuristics
    for doc in docs:
        for cit in doc.citations:
            parse_citation_heuristic(cit)

    # Pass 2: DOI enrichment
    if use_doi:
        seen_dois: dict[str, dict[str, object]] = {}
        for doc in docs:
            for cit in doc.citations:
                doi = cit.get("doi")
                if not doi:
                    continue
                # Cache within batch: same DOI resolved once
                if doi not in seen_dois:
                    lookup = doi_lookup or _default_doi_lookup
                    seen_dois[doi] = lookup(doi)
                meta = seen_dois[doi]
                if meta:
                    field_map = {
                        "title": "title", "authors": "authors",
                        "journal": "venue", "venue": "venue",
                        "volume": "volume", "pages": "pages",
                        "publisher": "publisher",
                    }
                    for src, dst in field_map.items():
                        val = meta.get(src)
                        if val:
                            cit[dst] = val
                    cit["doi_resolved"] = True

    # Pass 3: cross-paper fusion
    all_cits = [doc.citations for doc in docs]
    fuse_cross_paper_evidence(all_cits)
