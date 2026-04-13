"""Async citation resolver backed by the OpenAlex API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from asyncio import Semaphore
from functools import wraps
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from rapidfuzz import fuzz

from .bibtex import openalex_to_bibtex
from .db import DatabaseManager
from .models import ResolutionResult, Work

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"

# Fields to request from OpenAlex (keeps responses small)
_SELECT = ",".join([
    "id",
    "doi",
    "title",
    "publication_year",
    "authorships",
    "biblio",
    "primary_location",
    "cited_by_count",
    "referenced_works",
    "type",
])

# Max OpenAlex IDs per bulk-fetch call (~8KB URL limit, ~100-150 safe)
_BULK_BATCH_SIZE = 100

# Backoff settings
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 60.0


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_openalex_id(url: str) -> str:
    """Extract bare ID like 'W1234' from 'https://openalex.org/W1234'."""
    return url.rsplit("/", 1)[-1] if "/" in url else url


# ---------------------------------------------------------------------------
# Decorators for rate limiting and concurrency control
# ---------------------------------------------------------------------------


def add_limiter(limiter: AsyncLimiter):
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with limiter:
                return await func(*args, **kwargs)
        return wrapper
    return inner


def add_semaphore(semaphore: Semaphore):
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with semaphore:
                return await func(*args, **kwargs)
        return wrapper
    return inner


# ---------------------------------------------------------------------------
# OpenAlex response parser
# ---------------------------------------------------------------------------


def parse_openalex_work(item: dict) -> Work:
    """Parse an OpenAlex API response item into a Work."""
    authorships = item.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if a.get("author", {}).get("display_name")
    ]

    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = item.get("biblio") or {}

    doi_raw = item.get("doi") or ""
    doi = doi_raw.replace("https://doi.org/", "")

    oa_id_raw = item.get("id") or ""
    oa_id = _extract_openalex_id(oa_id_raw)

    return Work(
        doi=doi,
        openalex_id=oa_id,
        title=item.get("title") or "",
        year=item.get("publication_year"),
        journal=source.get("display_name") or "",
        authors=authors,
        volume=biblio.get("volume") or "",
        issue=biblio.get("issue") or "",
        first_page=biblio.get("first_page") or "",
        last_page=biblio.get("last_page") or "",
        publisher=source.get("host_organization_name") or "",
        cited_by_count=item.get("cited_by_count"),
        work_type=item.get("type") or "",
        bibtex=openalex_to_bibtex(item),
        raw=item,
    )


class AsyncResolver:
    """Resolve citations against the OpenAlex API with local SQLite caching.

    Resolution waterfall per citation:
      Level A -- DOI direct lookup
      Level B -- Fielded query (author names + year)
      Level C -- Fuzzy full-text search (rapidfuzz > 85)

    After resolving a batch, auto-expands depth-1 referenced_works.
    """

    def __init__(
        self,
        db: DatabaseManager,
        *,
        email: str,
        max_concurrent: int = 3,
        requests_per_second: float = 3.0,
        expand_references: bool = True,
        confidence_threshold: float = 85.0,
    ) -> None:
        self.db = db
        self.email = email
        self.expand_references = expand_references
        self.confidence_threshold = confidence_threshold
        self._client: httpx.AsyncClient | None = None

        # Rate control: decorators wrap the single-attempt _fetch_raw into
        # _fetch_once. The retrying _fetch method calls _fetch_once per attempt,
        # so the semaphore is released between retries.
        limiter = AsyncLimiter(1, round(1 / requests_per_second, 3))
        semaphore = Semaphore(value=max_concurrent)
        self._fetch_once = add_limiter(limiter)(add_semaphore(semaphore)(self._fetch_raw))

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": f"citestore/1.0 (mailto:{self.email})",
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---- HTTP layer ----

    async def _fetch_raw(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Single HTTP GET — called through the decorated self._fetch_once."""
        client = await self._ensure_client()
        return await client.get(url, params=params)

    async def _fetch(self, url: str, params: dict[str, Any] | None = None) -> dict | None:
        """GET with retries. Each attempt goes through the decorated _fetch_once
        so the semaphore is released between retries."""
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._fetch_once(url, params)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 503):
                    wait = min(
                        _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1),
                        _BACKOFF_MAX,
                    )
                    logger.warning(
                        "OpenAlex %d, retry %d/%d in %.1fs",
                        resp.status_code, attempt + 1, _MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.warning("OpenAlex HTTP %d for %s", resp.status_code, url)
                return None
            except httpx.HTTPError as exc:
                wait = min(
                    _BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1),
                    _BACKOFF_MAX,
                )
                logger.warning(
                    "HTTP error %s, retry %d/%d in %.1fs",
                    exc, attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
        return None

    # ---- Single-work resolution ----

    async def _resolve_by_doi(self, doi: str) -> Work | None:
        """Level A: direct DOI lookup."""
        data = await self._fetch(
            f"{OPENALEX_BASE}/works",
            {"filter": f"doi:{doi}", "select": _SELECT, "per_page": "1"},
        )
        if not data:
            return None
        results = data.get("results") or []
        if not results:
            return None
        return parse_openalex_work(results[0])

    async def _resolve_by_query(
        self, author_names: list[str], year: int | None
    ) -> Work | None:
        """Level B: fielded search by author + year."""
        if not author_names:
            return None
        search_text = " ".join(author_names[:3])
        params: dict[str, str] = {
            "search": search_text,
            "select": _SELECT,
            "per_page": "5",
        }
        if year:
            params["filter"] = f"publication_year:{year}"
        data = await self._fetch(f"{OPENALEX_BASE}/works", params)
        if not data:
            return None
        results = data.get("results") or []
        if not results:
            return None
        return parse_openalex_work(results[0])

    async def _resolve_by_fuzzy(self, raw_text: str) -> tuple[Work | None, float]:
        """Level C: fuzzy full-text search with confidence scoring."""
        query = raw_text[:200]
        data = await self._fetch(
            f"{OPENALEX_BASE}/works",
            {"search": query, "select": _SELECT, "per_page": "5"},
        )
        if not data:
            return None, 0.0
        results = data.get("results") or []
        if not results:
            return None, 0.0

        best_work = None
        best_score = 0.0
        for item in results:
            candidate_title = item.get("title") or ""
            score = fuzz.token_sort_ratio(raw_text[:300], candidate_title)
            if score > best_score:
                best_score = score
                best_work = item

        if best_work and best_score >= self.confidence_threshold:
            return parse_openalex_work(best_work), best_score
        return None, best_score

    # ---- Waterfall resolution ----

    async def _resolve_one(self, cit: dict) -> ResolutionResult:
        """Run the A/B/C waterfall for a single citation dict."""
        raw_text = cit.get("raw_text") or ""
        doi = cit.get("doi") or ""
        year = cit.get("year")
        author_last_names = cit.get("author_last_names") or []

        text_hash = _sha256(raw_text) if raw_text else ""

        # Check string_cache
        if text_hash:
            cached = await self.db.get_cached_resolution(text_hash)
            if cached is not None:
                resolved_doi, level = cached
                work = None
                if resolved_doi:
                    work = await self.db.get_work(resolved_doi)
                return ResolutionResult(
                    work=work, level=level,
                    source_doi=doi, source_text=raw_text,
                )

        # Check works table by DOI
        if doi:
            existing = await self.db.get_work(doi)
            if existing:
                if text_hash:
                    await self.db.cache_resolution(text_hash, raw_text, doi, "A")
                return ResolutionResult(
                    work=existing, level="A",
                    source_doi=doi, source_text=raw_text,
                )

        # Level A: DOI direct
        if doi:
            work = await self._resolve_by_doi(doi)
            if work:
                await self.db.upsert_work(work)
                if text_hash:
                    await self.db.cache_resolution(text_hash, raw_text, work.doi, "A")
                return ResolutionResult(
                    work=work, level="A",
                    source_doi=doi, source_text=raw_text,
                )

        # Level B: fielded query
        if author_last_names and year:
            work = await self._resolve_by_query(author_last_names, year)
            if work:
                # Validate with fuzzy match if we have raw_text
                if raw_text:
                    score = fuzz.token_sort_ratio(raw_text[:300], work.title)
                    if score < self.confidence_threshold:
                        work = None
                if work:
                    await self.db.upsert_work(work)
                    if text_hash:
                        await self.db.cache_resolution(
                            text_hash, raw_text, work.doi, "B"
                        )
                    return ResolutionResult(
                        work=work, level="B",
                        source_doi=doi, source_text=raw_text,
                    )

        # Level C: fuzzy search
        if raw_text:
            work, _score = await self._resolve_by_fuzzy(raw_text)
            if work:
                await self.db.upsert_work(work)
                if text_hash:
                    await self.db.cache_resolution(text_hash, raw_text, work.doi, "C")
                return ResolutionResult(
                    work=work, level="C",
                    source_doi=doi, source_text=raw_text,
                )

        # Miss
        if text_hash:
            await self.db.cache_resolution(text_hash, raw_text, None, "miss")
        return ResolutionResult(
            work=None, level="miss",
            source_doi=doi, source_text=raw_text,
        )

    # ---- Batch resolution ----

    async def resolve_batch(
        self,
        citations: list[dict],
        *,
        progress_callback: Any | None = None,
    ) -> list[ResolutionResult]:
        """Resolve a batch of citation dicts through the waterfall.

        Each dict should have: raw_text, doi (optional), year (optional),
        author_last_names (optional). This matches the output of
        wikify.ingest.citations.extract_citations().

        Uses asyncio.gather with semaphore + rate limiter decorators on
        the HTTP layer to control concurrency and throughput.
        """
        raw_results = await asyncio.gather(
            *(self._resolve_one(c) for c in citations),
            return_exceptions=True,
        )
        results: list[ResolutionResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                logger.warning("Resolution failed for citation %d: %s", i, r)
                results.append(ResolutionResult(
                    work=None, level="miss",
                    source_doi=citations[i].get("doi", ""),
                    source_text=citations[i].get("raw_text", ""),
                ))
            else:
                results.append(r)
            if progress_callback:
                progress_callback(i + 1, len(citations), results[-1])

        if self.expand_references:
            await self._expand_references(results)

        return results

    # ---- Depth-1 reference expansion ----

    async def _expand_references(self, results: list[ResolutionResult]) -> None:
        """Bulk-fetch metadata for all referenced_works of resolved papers."""
        # Collect all referenced OpenAlex IDs
        parent_refs: dict[str, list[str]] = {}  # parent_doi -> [child oa_ids]
        for r in results:
            if not r.work or not r.work.raw:
                continue
            ref_urls = r.work.raw.get("referenced_works") or []
            if ref_urls:
                oa_ids = [_extract_openalex_id(u) for u in ref_urls]
                parent_refs[r.work.doi] = oa_ids

        if not parent_refs:
            return

        # Collect all unique OA IDs, filter already-known
        all_oa_ids = set()
        for ids in parent_refs.values():
            all_oa_ids.update(ids)

        known = await self.db.known_openalex_ids()
        to_fetch = [oa for oa in all_oa_ids if oa not in known]

        logger.info(
            "Expanding references: %d unique IDs, %d to fetch (%d already cached)",
            len(all_oa_ids), len(to_fetch), len(all_oa_ids) - len(to_fetch),
        )

        # Bulk-fetch in batches
        fetched_works: dict[str, Work] = {}
        for batch_start in range(0, len(to_fetch), _BULK_BATCH_SIZE):
            batch = to_fetch[batch_start : batch_start + _BULK_BATCH_SIZE]
            works = await self._bulk_fetch_by_openalex_ids(batch)
            for w in works:
                fetched_works[w.openalex_id] = w

        # Store all fetched works
        if fetched_works:
            await self.db.upsert_works(list(fetched_works.values()))

        # Build edges: map OA IDs to DOIs for already-known works too
        oa_to_doi: dict[str, str] = {w.openalex_id: w.doi for w in fetched_works.values()}
        # Also map known works
        for oa_id in all_oa_ids - set(to_fetch):
            existing = await self.db.get_work_by_openalex(oa_id)
            if existing:
                oa_to_doi[existing.openalex_id] = existing.doi

        for parent_doi, child_oa_ids in parent_refs.items():
            child_dois = [
                oa_to_doi[oa] for oa in child_oa_ids if oa in oa_to_doi
            ]
            if child_dois:
                await self.db.add_edges(parent_doi, child_dois)

    async def _bulk_fetch_by_openalex_ids(self, oa_ids: list[str]) -> list[Work]:
        """Fetch multiple works in one API call using pipe-separated IDs."""
        if not oa_ids:
            return []
        filter_val = "|".join(oa_ids)
        data = await self._fetch(
            f"{OPENALEX_BASE}/works",
            {
                "filter": f"openalex:{filter_val}",
                "select": _SELECT,
                "per_page": "200",
            },
        )
        if not data:
            return []
        results = data.get("results") or []
        return [parse_openalex_work(item) for item in results]
