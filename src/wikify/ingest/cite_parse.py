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


_CROSSREF_BATCH_SIZE = 75


def _parse_crossref_item(item: dict) -> dict[str, object]:
    """Convert a CrossRef API work item to our standard metadata dict."""
    titles = item.get("title") or []
    title = titles[0] if titles else ""
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in item.get("author") or []
    ]
    journals = item.get("container-title") or []
    journal = journals[0] if journals else ""
    # Year: try published-print, then published-online, then issued
    year = ""
    for date_key in ("published-print", "published-online", "issued"):
        parts = (item.get(date_key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break
    biblio = {
        "title": title,
        "authors": authors,
        "journal": journal,
        "venue": journal,
        "year": year,
        "volume": item.get("volume") or "",
        "pages": item.get("page") or "",
        "publisher": item.get("publisher") or "",
    }
    return {k: v for k, v in biblio.items() if v}


async def _resolve_dois_async(dois: list[str]) -> dict[str, dict[str, object]]:
    """Resolve DOIs via CrossRef batch JSON API.

    Uses ``/works?filter=doi:x,doi:y,...`` to fetch up to 75 DOIs per
    call. ~200 DOIs/s.  For 350 DOIs: ~1.5s in 5 API calls.
    """
    import httpx

    limiter = AsyncLimiter(1, 1 / 5)
    semaphore = Semaphore(value=3)

    @_add_limiter(limiter)
    @_add_semaphore(semaphore)
    async def fetch_batch(
        client: httpx.AsyncClient, batch: list[str],
    ) -> list[dict]:
        filter_val = ",".join(f"doi:{d}" for d in batch)
        resp = await client.get(
            "https://api.crossref.org/works",
            params={
                "filter": filter_val,
                "rows": str(len(batch)),
                "select": "DOI,title,author,container-title,volume,page,"
                "published-print,published-online,issued,publisher",
            },
        )
        if resp.status_code != 200:
            return []
        return (resp.json().get("message") or {}).get("items") or []

    batches = [
        dois[i : i + _CROSSREF_BATCH_SIZE]
        for i in range(0, len(dois), _CROSSREF_BATCH_SIZE)
    ]

    async with httpx.AsyncClient(
        timeout=20.0,
        headers={"User-Agent": "wikify/1.0 (mailto:wikify@example.com)"},
    ) as client:
        results = await asyncio.gather(
            *(fetch_batch(client, b) for b in batches),
            return_exceptions=True,
        )

    out: dict[str, dict[str, object]] = {}
    for batch_result in results:
        if isinstance(batch_result, BaseException):
            logger.debug("CrossRef batch failed: %s", batch_result)
            continue
        for item in batch_result:
            doi = (item.get("DOI") or "").lower()
            if doi:
                out[doi] = _parse_crossref_item(item)
    return out


async def _resolve_dois_doiorg(dois: list[str]) -> dict[str, dict[str, object]]:
    """Fallback: resolve DOIs via doi.org content negotiation (BibTeX).

    Slower than CrossRef batch (~5 DOIs/s) but catches DOIs not in CrossRef.
    """
    import random

    import httpx

    from .bibtex import _metadata_from_bibtex_entry

    limiter = AsyncLimiter(1, 1 / 8)
    semaphore = Semaphore(value=5)

    @_add_limiter(limiter)
    @_add_semaphore(semaphore)
    async def fetch_once(client: httpx.AsyncClient, doi: str) -> httpx.Response:
        return await client.get(
            f"https://doi.org/{doi}",
            headers={"Accept": "application/x-bibtex"},
        )

    async def fetch(client: httpx.AsyncClient, doi: str) -> dict:
        for _attempt in range(3):
            try:
                resp = await fetch_once(client, doi)
                if resp.status_code == 200:
                    return _metadata_from_bibtex_entry(resp.text)
                if resp.status_code == 429:
                    await asyncio.sleep(0.5 + random.uniform(0, 0.5))
                    continue
                return {}
            except httpx.HTTPError:
                await asyncio.sleep(0.5)
        return {}

    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=True,
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
    ) as client:
        results = await asyncio.gather(
            *(fetch(client, d) for d in dois), return_exceptions=True,
        )

    out: dict[str, dict[str, object]] = {}
    for doi, result in zip(dois, results):
        if isinstance(result, BaseException):
            continue
        if result:
            out[doi.lower()] = result
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
    # Pass 0: re-extract DOIs from raw_text (fixes truncated DOIs from old ingests)
    from ..citestore.parse import _clean_doi
    from ..citestore.parse import extract_doi as _extract_doi_from_text
    for doc in docs:
        for cit in doc.citations:
            if cit.doi and not _clean_doi(cit.doi):
                # Stored DOI is truncated/invalid — try re-extracting
                better = _extract_doi_from_text(cit.raw_text)
                cit.doi = better
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

    # Pass 2: DOI resolution (async batch via CrossRef + doi.org fallback)
    if use_doi:
        unique_dois = sorted({
            cit.doi for doc in docs for cit in doc.citations if cit.doi
        })
        if unique_dois:
            if doi_lookup:
                doi_meta = {doi: doi_lookup(doi) for doi in unique_dois}
            else:
                logger.info(
                    "Resolving %d unique DOIs via CrossRef batch...",
                    len(unique_dois),
                )
                doi_meta = asyncio.run(_resolve_dois_async(unique_dois))
                # Fallback: doi.org content negotiation for misses
                missed = [d for d in unique_dois if not doi_meta.get(d.lower())]
                if missed:
                    logger.info(
                        "Falling back to doi.org for %d/%d unresolved DOIs...",
                        len(missed), len(unique_dois),
                    )
                    fallback = asyncio.run(_resolve_dois_doiorg(missed))
                    doi_meta.update(fallback)

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
