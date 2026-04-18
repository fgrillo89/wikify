"""Adapter: enrich wikify Document citations using citestore.parse.

Thin wrapper that bridges citestore's standalone citation parser to
wikify's Document model and the shared DOI resolver.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..citestore.parse import parse_citation

if TYPE_CHECKING:
    from ..models import Document

_DOI_FIELD_MAP = {
    "title": "title",
    "authors": "authors",
    "journal": "venue",
    "venue": "venue",
    "volume": "volume",
    "pages": "pages",
    "publisher": "publisher",
}


# ---------------------------------------------------------------------------
# Cross-paper evidence fusion
# ---------------------------------------------------------------------------

def _fuse_citations(docs: list[Document]) -> None:
    """Cross-paper evidence fusion: merge metadata for same-work citations."""
    from collections import Counter, defaultdict

    from ..citestore.parse import _is_valid_title, _is_valid_venue, citation_fingerprint

    buckets: dict[str, list] = defaultdict(list)
    for doc in docs:
        for cit in doc.citations:
            d = cit.to_dict()
            fp = citation_fingerprint(d)
            if fp:
                buckets[fp].append(cit)

    for _fp, group in buckets.items():
        if len(group) < 2:
            continue
        merged: dict[str, object] = {}
        for field in ("title", "authors", "venue", "volume", "pages", "doi", "year"):
            values = [getattr(c, field) for c in group if getattr(c, field)]
            if not values:
                continue
            if field in ("title", "authors"):
                best = max(values, key=lambda v: len(v) if isinstance(v, (str, list)) else 0)
            elif field == "doi":
                best = values[0]
            elif field == "year":
                best = Counter(values).most_common(1)[0][0]
            else:
                hashable = [tuple(v) if isinstance(v, list) else v for v in values]
                best = Counter(hashable).most_common(1)[0][0]
            if field == "title" and not (isinstance(best, str) and _is_valid_title(best)):
                continue
            if field == "venue" and not (isinstance(best, str) and _is_valid_venue(best)):
                continue
            merged[field] = best
        for cit in group:
            for key, value in merged.items():
                if value and not getattr(cit, key, None):
                    setattr(cit, key, value)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def enrich_citations(
    docs: list[Document],
    *,
    cache_path: Path,
    use_doi: bool = True,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
    skip_content_neg: bool = False,
) -> None:
    """Enrich all citations across all documents in-place.

    Four passes:
    0. Re-extract DOIs from raw_text (fixes truncated DOIs)
    1. Heuristic extraction via citestore.parse (zero API calls)
    2. DOI resolution via the shared resolver: cache -> CrossRef batch
       -> doi.org fallback -> negative-cache. One code path for all
       DOI lookups in wikify. When ``skip_content_neg`` is True, step 3
       of the resolver (doi.org fallback) is suppressed for speed.
    3. Cross-paper evidence fusion
    """
    from ..citestore.parse import _clean_doi
    from ..citestore.parse import extract_doi as _extract_doi_from_text
    from ..util.doi_resolver import resolve_many

    # Pass 0: re-extract DOIs from raw_text
    for doc in docs:
        for cit in doc.citations:
            if cit.doi and not _clean_doi(cit.doi):
                cit.doi = _extract_doi_from_text(cit.raw_text)
            elif not cit.doi:
                cit.doi = _extract_doi_from_text(cit.raw_text)

    # Pass 1: heuristic parsing
    for doc in docs:
        for cit in doc.citations:
            if len(cit.title) >= 15 and len(cit.authors) >= 1:
                continue
            parsed = parse_citation(cit.raw_text, year=cit.year)
            for key, val in parsed.items():
                if not val:
                    continue
                old = getattr(cit, key, None)
                if not old or (isinstance(old, str) and len(old) < 10):
                    setattr(cit, key, val)

    # Pass 2: DOI resolution
    if use_doi:
        unique_dois = sorted({
            cit.doi for doc in docs for cit in doc.citations if cit.doi
        })
        if unique_dois:
            if doi_lookup is not None:
                # Explicit lookup injection (tests).
                doi_meta = {
                    d.lower(): doi_lookup(d) for d in unique_dois
                }
            else:
                doi_meta = resolve_many(
                    unique_dois,
                    cache_path=cache_path,
                    skip_content_neg=skip_content_neg,
                )

            for doc in docs:
                for cit in doc.citations:
                    if not cit.doi:
                        continue
                    meta = doi_meta.get(cit.doi.lower())
                    if not meta:
                        continue
                    for src, dst in _DOI_FIELD_MAP.items():
                        val = meta.get(src)
                        if val:
                            setattr(cit, dst, val)
                    cit.resolution = cit.resolution or "doi"

    # Pass 3: cross-paper fusion
    _fuse_citations(docs)
