"""Unified DOI resolution with CrossRef-first + doi.org fallback.

Single entry point for every DOI lookup in wikify. Both the corpus-paper
path (wave D, bibtex.py) and the reference-citation path (wave B,
cite_parse.py) call this module — identical strategy for both:

  1. Cache lookup (SQLite ``works`` table at ``<corpus>/.citestore.db``).
     Negative-result rows count as cache hits so failures aren't
     re-fetched on every refresh.
  2. For misses: CrossRef batch (``/works?filter=doi:X,Y,...``,
     75 DOIs/call, structured JSON output).
  3. For DOIs CrossRef still missed or returned incomplete data for:
     doi.org content negotiation (``Accept: application/x-bibtex``,
     one request per DOI, covers non-CrossRef registration agents
     like DataCite / mEDRA / JaLC).
  4. Persist all results — including negatives — to the DOICache.

Every outbound request is gated by ``AsyncLimiter`` (polite qps floor)
and ``Semaphore`` (hard concurrency cap) via the shared decorators in
``wikify.util.async_limits``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import httpx
from aiolimiter import AsyncLimiter

from ..citestore.db import DOICache
from .async_limits import with_limiter, with_semaphore

logger = logging.getLogger(__name__)

_CROSSREF_BATCH_SIZE = 75
_CROSSREF_SELECT = (
    "DOI,title,author,container-title,volume,page,"
    "published-print,published-online,issued,publisher"
)


def resolve_many(
    dois: list[str],
    *,
    cache_path: Path,
    crossref_concurrency: int = 3,
    crossref_qps: float = 5.0,
    doiorg_concurrency: int = 5,
    doiorg_qps: float = 8.0,
    timeout: float = 15.0,
) -> dict[str, dict[str, object]]:
    """Resolve many DOIs. Cache -> CrossRef batch -> doi.org fallback.

    Returns ``{lowercased_doi: metadata}`` with one entry per DOI that
    was attempted (resolved OR negatively cached). A row whose title
    is empty means "we tried, no source had data" — subsequent calls
    short-circuit via the cache.
    """
    if not dois:
        return {}
    unique = list(dict.fromkeys(d.lower() for d in dois if d))
    if not unique:
        return {}

    # Step 1 — cache lookup.
    with DOICache(cache_path) as cache:
        results = cache.get_many(unique)
    to_fetch = [d for d in unique if d not in results]
    if not to_fetch:
        return results

    # Step 2 — CrossRef batch for everything uncached.
    logger.info(
        "DOI resolve: %d cached, %d via CrossRef + doi.org fallback",
        len(results), len(to_fetch),
    )
    xref = asyncio.run(
        _crossref_batch(
            to_fetch,
            concurrency=crossref_concurrency,
            qps=crossref_qps,
            timeout=timeout,
        ),
    )

    # Step 3 — doi.org fallback for CrossRef misses and incomplete records.
    missed = [d for d in to_fetch if not _is_complete(xref.get(d))]
    fallback: dict[str, dict[str, object]] = {}
    if missed:
        fallback = asyncio.run(
            _doiorg_fallback(
                missed,
                concurrency=doiorg_concurrency,
                qps=doiorg_qps,
                timeout=timeout,
            ),
        )

    # Merge: prefer CrossRef (richer) when both returned, fall back to
    # doi.org, then negative-cache whatever remains.
    fresh: dict[str, dict[str, object]] = {}
    for doi in to_fetch:
        if _is_complete(xref.get(doi)):
            fresh[doi] = xref[doi]
        elif fallback.get(doi):
            fresh[doi] = fallback[doi]
        else:
            fresh[doi] = {}

    # Step 4 — persist (including negatives). Source tag on the row
    # records which path actually produced the data, for diagnostics.
    with DOICache(cache_path) as cache:
        for doi, meta in fresh.items():
            if _is_complete(meta):
                src = "crossref" if doi in xref and xref[doi] else "doi.org"
            else:
                src = "not-found"
            cache.put(doi, meta, src)

    results.update(fresh)
    return results


def _is_complete(meta: dict[str, object] | None) -> bool:
    """A DOI record is useful when it has at least a title + one author.

    Title-only records are common from doi.org when the RA returns a
    terse response; without authors they can't build a bib entry that
    passes downstream validation, so we treat them as incomplete and
    retry via the other source.
    """
    if not meta:
        return False
    title = str(meta.get("title") or "").strip()
    authors = meta.get("authors") or []
    return bool(title) and bool(authors)


# ---------------------------------------------------------------------------
# CrossRef batch fetcher
# ---------------------------------------------------------------------------

def _parse_crossref_item(item: dict) -> dict[str, object]:
    titles = item.get("title") or []
    title = titles[0] if titles else ""
    authors = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in item.get("author") or []
    ]
    journals = item.get("container-title") or []
    journal = journals[0] if journals else ""
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


async def _crossref_batch(
    dois: list[str], *, concurrency: int, qps: float, timeout: float,
) -> dict[str, dict[str, object]]:
    limiter = AsyncLimiter(1, round(1 / qps, 3))
    semaphore = asyncio.Semaphore(concurrency)

    batches = [
        dois[i : i + _CROSSREF_BATCH_SIZE]
        for i in range(0, len(dois), _CROSSREF_BATCH_SIZE)
    ]

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "wikify/1.0 (mailto:wikify@example.com)"},
    ) as client:

        @with_limiter(limiter)
        @with_semaphore(semaphore)
        async def fetch_batch(batch: list[str]) -> list[dict]:
            resp = await client.get(
                "https://api.crossref.org/works",
                params={
                    "filter": ",".join(f"doi:{d}" for d in batch),
                    "rows": str(len(batch)),
                    "select": _CROSSREF_SELECT,
                },
            )
            if resp.status_code != 200:
                return []
            return (resp.json().get("message") or {}).get("items") or []

        results = await asyncio.gather(
            *(fetch_batch(b) for b in batches), return_exceptions=True,
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


# ---------------------------------------------------------------------------
# doi.org content-negotiation fallback
# ---------------------------------------------------------------------------

async def _doiorg_fallback(
    dois: list[str], *, concurrency: int, qps: float, timeout: float,
) -> dict[str, dict[str, object]]:
    from ..ingest.bibtex import _metadata_from_bibtex_entry

    limiter = AsyncLimiter(1, round(1 / qps, 3))
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=concurrency,
            max_keepalive_connections=concurrency,
        ),
    ) as client:

        @with_limiter(limiter)
        @with_semaphore(semaphore)
        async def fetch(doi: str) -> dict[str, object]:
            for _attempt in range(3):
                try:
                    resp = await client.get(
                        f"https://doi.org/{doi}",
                        headers={"Accept": "application/x-bibtex"},
                    )
                except httpx.HTTPError:
                    await asyncio.sleep(0.5)
                    continue
                if resp.status_code == 200:
                    return _metadata_from_bibtex_entry(resp.text)
                if resp.status_code == 429:
                    await asyncio.sleep(0.5 + random.uniform(0, 0.5))
                    continue
                return {}
            return {}

        results = await asyncio.gather(
            *(fetch(d) for d in dois), return_exceptions=True,
        )

    out: dict[str, dict[str, object]] = {}
    for doi, result in zip(dois, results, strict=True):
        if isinstance(result, BaseException):
            continue
        if result:
            out[doi.lower()] = result
    return out


def resolve_one(doi: str, *, cache_path: Path) -> dict[str, object]:
    """Resolve a single DOI. Thin wrapper around ``resolve_many``."""
    if not doi:
        return {}
    return resolve_many([doi], cache_path=cache_path).get(doi.lower(), {})
