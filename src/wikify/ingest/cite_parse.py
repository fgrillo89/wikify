"""Adapter: enrich wikify Document citations using citestore.parse.

Thin wrapper that bridges citestore's standalone citation parser to
wikify's Document model and DOI content negotiation from bibtex.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..citestore.parse import parse_citation

if TYPE_CHECKING:
    from ..models import Document


def _default_doi_lookup(doi: str) -> dict[str, object]:
    from .bibtex import resolve_doi_metadata
    return resolve_doi_metadata(doi)


_DOI_FIELD_MAP = {
    "title": "title",
    "authors": "authors",
    "journal": "venue",
    "venue": "venue",
    "volume": "volume",
    "pages": "pages",
    "publisher": "publisher",
}


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
        # Build merged record from best values
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
            # Validate before propagating
            if field == "title" and not (isinstance(best, str) and _is_valid_title(best)):
                continue
            if field == "venue" and not (isinstance(best, str) and _is_valid_venue(best)):
                continue
            merged[field] = best
        # Write back: fill empty fields only
        for cit in group:
            for key, value in merged.items():
                if value and not getattr(cit, key, None):
                    setattr(cit, key, value)


def enrich_citations(
    docs: list[Document],
    *,
    use_doi: bool = True,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> None:
    """Enrich all citations across all documents in-place.

    Three passes:
    1. Heuristic extraction via citestore.parse (zero API calls)
    2. DOI content negotiation (free, no API key)
    3. Cross-paper evidence fusion
    """
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

    # Pass 2: DOI content negotiation
    if use_doi:
        lookup = doi_lookup or _default_doi_lookup
        seen: dict[str, dict[str, object]] = {}
        for doc in docs:
            for cit in doc.citations:
                if not cit.doi:
                    continue
                if cit.doi not in seen:
                    seen[cit.doi] = lookup(cit.doi)
                meta = seen[cit.doi]
                if not meta:
                    continue
                for src, dst in _DOI_FIELD_MAP.items():
                    val = meta.get(src)
                    if val:
                        setattr(cit, dst, val)
                cit.resolution = cit.resolution or "doi"

    # Pass 3: cross-paper fusion
    _fuse_citations(docs)
