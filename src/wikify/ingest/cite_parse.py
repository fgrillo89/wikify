"""Adapter: enrich wikify Document citations using citestore.parse.

Thin wrapper that bridges citestore's standalone citation parser to
wikify's Document model and DOI content negotiation from bibtex.py.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import Semaphore
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING

from aiolimiter import AsyncLimiter

from ..citestore.parse import parse_citation

if TYPE_CHECKING:
    from ..models import Document

logger = logging.getLogger(__name__)

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
# Async DOI content negotiation
# ---------------------------------------------------------------------------

def _add_limiter(limiter: AsyncLimiter):
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with limiter:
                return await func(*args, **kwargs)
        return wrapper
    return inner


def _add_semaphore(semaphore: Semaphore):
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with semaphore:
                return await func(*args, **kwargs)
        return wrapper
    return inner


async def _resolve_dois_async(dois: list[str]) -> dict[str, dict[str, object]]:
    """Resolve all unique DOIs concurrently via content negotiation."""
    import httpx

    from .bibtex import _metadata_from_bibtex_entry

    limiter = AsyncLimiter(1, 1 / 50)  # 50 req/s
    semaphore = Semaphore(value=30)

    @_add_limiter(limiter)
    @_add_semaphore(semaphore)
    async def fetch_one(client: httpx.AsyncClient, doi: str) -> dict:
        resp = await client.get(
            f"https://doi.org/{doi}",
            headers={"Accept": "application/x-bibtex"},
        )
        if resp.status_code != 200:
            return {}
        return _metadata_from_bibtex_entry(resp.text)

    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=True,
        limits=httpx.Limits(max_connections=30, max_keepalive_connections=20),
    ) as client:
        results = await asyncio.gather(
            *(fetch_one(client, doi) for doi in dois),
            return_exceptions=True,
        )

    out: dict[str, dict[str, object]] = {}
    for doi, result in zip(dois, results):
        if isinstance(result, BaseException):
            logger.debug("DOI negotiation failed for %s: %s", doi, result)
            out[doi] = {}
        else:
            out[doi] = result
    return out


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
    use_doi: bool = True,
    doi_lookup: Callable[[str], dict[str, object]] | None = None,
) -> None:
    """Enrich all citations across all documents in-place.

    Three passes:
    1. Heuristic extraction via citestore.parse (zero API calls)
    2. DOI content negotiation (async, 50 req/s, free, no API key)
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

    # Pass 2: DOI content negotiation (async)
    if use_doi:
        unique_dois = sorted({
            cit.doi for doc in docs for cit in doc.citations if cit.doi
        })
        if unique_dois:
            if doi_lookup:
                # Sync fallback for custom lookup
                doi_meta = {doi: doi_lookup(doi) for doi in unique_dois}
            else:
                logger.info("Resolving %d unique DOIs via content negotiation...", len(unique_dois))
                doi_meta = asyncio.run(_resolve_dois_async(unique_dois))

            for doc in docs:
                for cit in doc.citations:
                    if not cit.doi:
                        continue
                    meta = doi_meta.get(cit.doi)
                    if not meta:
                        continue
                    for src, dst in _DOI_FIELD_MAP.items():
                        val = meta.get(src)
                        if val:
                            setattr(cit, dst, val)
                    cit.resolution = cit.resolution or "doi"

    # Pass 3: cross-paper fusion
    _fuse_citations(docs)
