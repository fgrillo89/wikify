"""Adapter: enrich wikify Document citations using citestore.parse.

Thin wrapper that bridges citestore's standalone citation parser to
wikify's Document model and DOI content negotiation from bibtex.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..citestore.parse import (
    fuse_cross_paper_evidence,
    parse_citation,
)

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
            existing_title = cit.get("title") or ""
            existing_authors = cit.get("authors") or []
            # Skip if already has a real title (>= 15 chars) and authors
            if len(existing_title) >= 15 and len(existing_authors) >= 1:
                continue
            parsed = parse_citation(
                cit.get("raw_text", ""), year=cit.get("year"),
            )
            for key, val in parsed.items():
                if not val:
                    continue
                old = cit.get(key)
                # Overwrite if empty, or if old value is short garbage
                if not old or (isinstance(old, str) and len(old) < 10):
                    cit[key] = val

    # Pass 2: DOI content negotiation
    if use_doi:
        lookup = doi_lookup or _default_doi_lookup
        seen: dict[str, dict[str, object]] = {}
        for doc in docs:
            for cit in doc.citations:
                doi = cit.get("doi")
                if not doi:
                    continue
                if doi not in seen:
                    seen[doi] = lookup(doi)
                meta = seen[doi]
                if not meta:
                    continue
                for src, dst in _DOI_FIELD_MAP.items():
                    val = meta.get(src)
                    if val:
                        cit[dst] = val
                cit["doi_resolved"] = True

    # Pass 3: cross-paper fusion
    fuse_cross_paper_evidence([doc.citations for doc in docs])
