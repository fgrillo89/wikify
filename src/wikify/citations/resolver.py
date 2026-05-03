"""Async citation resolver backed by the OpenAlex API.

Strategy: resolve corpus papers by DOI in bulk, then bulk-fetch their
referenced_works. Citation texts are matched locally to resolved works
-- no per-citation API calls needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from asyncio import Semaphore
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from rapidfuzz import fuzz, process

from ..util.async_limits import with_limiter as add_limiter
from ..util.async_limits import with_semaphore as add_semaphore
from .bibtex import openalex_to_bibtex
from .db import DatabaseManager
from .models import ResolutionResult, Work

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"

_SELECT = ",".join([
    "id", "doi", "title", "publication_year", "authorships", "biblio",
    "primary_location", "cited_by_count", "referenced_works", "type",
])

_DOI_BATCH_SIZE = 50
_OA_BATCH_SIZE = 100
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 60.0

# Phase 4 (text fuzzy match) tuning. The naive cross-product cost is
# O(unresolved * candidates); on a real corpus that is hundreds of
# millions of comparisons. The inverted token index prunes candidates
# down to ~tens per bib, then `fuzz.partial_ratio` picks out a clean
# title window inside the noisy raw_text.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "for", "on", "with", "by",
    "to", "from", "at", "as", "is", "are", "was", "were", "be", "this",
    "that", "these", "those", "or", "but", "not", "into", "via",
    "vol", "no", "pp", "doi", "et", "al", "ed", "eds",
})
_MIN_TOKEN_LEN = 3
_MIN_TITLE_LEN = 25
_MIN_SHARED_TOKENS = 3
_RAW_WINDOW = 600
# Drop "memristor"-class tokens from the inverted index: they appear in
# thousands of titles, blow up candidate sets, and add no discriminative
# power. The scorer still sees them via partial_ratio over the raw_text.
_MAX_DF_RATIO = 0.05
# Cap candidates per bib to bound the Python -> C round trip in the
# rare case that a bib still pulls in too many post-DF-filter tokens.
_MAX_CANDIDATES = 200


def _significant_tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_openalex_id(url: str) -> str:
    return url.rsplit("/", 1)[-1] if "/" in url else url


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
    oa_id = _extract_openalex_id(item.get("id") or "")

    return Work(
        doi=doi, openalex_id=oa_id,
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
    """Resolve citations via OpenAlex with batch-first strategy.

    1. Bulk-resolve corpus paper DOIs (1 call per 50 DOIs)
    2. Collect referenced_works from responses (free, no extra calls)
    3. Bulk-fetch referenced work metadata (1 call per 100 IDs)
    4. Match citation texts to resolved works locally

    ~20 papers resolves in ~30 API calls total.
    """

    def __init__(
        self,
        db: DatabaseManager,
        *,
        email: str,
        max_concurrent: int = 5,
        requests_per_second: float = 5.0,
        expand_references: bool = True,
        confidence_threshold: float = 85.0,
    ) -> None:
        self.db = db
        self.email = email
        self.expand_references = expand_references
        self.confidence_threshold = confidence_threshold
        self._client: httpx.AsyncClient | None = None

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
        client = await self._ensure_client()
        return await client.get(url, params=params)

    async def _fetch(self, url: str, params: dict[str, Any] | None = None) -> dict | None:
        """GET with retries. Semaphore released between attempts."""
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

    # ---- Bulk fetch primitives ----

    async def _bulk_fetch_by_dois(self, dois: list[str]) -> dict[str, Work]:
        """Fetch works by pipe-separated DOI filter. Returns doi -> Work."""
        if not dois:
            return {}
        data = await self._fetch(
            f"{OPENALEX_BASE}/works",
            {"filter": f"doi:{"|".join(dois)}", "select": _SELECT, "per_page": "200"},
        )
        if not data:
            return {}
        return {w.doi: w for item in data.get("results") or []
                if (w := parse_openalex_work(item)) and w.doi}

    async def _bulk_fetch_by_openalex_ids(self, oa_ids: list[str]) -> list[Work]:
        """Fetch works by pipe-separated OpenAlex ID filter."""
        if not oa_ids:
            return []
        data = await self._fetch(
            f"{OPENALEX_BASE}/works",
            {"filter": f"openalex:{"|".join(oa_ids)}", "select": _SELECT, "per_page": "200"},
        )
        if not data:
            return []
        return [parse_openalex_work(item) for item in data.get("results") or []]

    # ---- Main resolution entry point ----

    async def resolve_batch(
        self,
        citations: list[dict],
        *,
        progress_callback: Any | None = None,
    ) -> list[ResolutionResult]:
        """Resolve citations using batch-first strategy.

        Phase 1: Check local cache
        Phase 2: Bulk DOI resolution (50 DOIs per API call)
        Phase 3: Bulk-fetch referenced_works (100 IDs per call) -- depth-1
        Phase 4: Match remaining citation texts locally against all resolved works
        """
        results: list[ResolutionResult | None] = [None] * len(citations)
        need_api: list[tuple[int, dict]] = []

        # ---- Phase 1: local cache ----
        for i, cit in enumerate(citations):
            raw_text = cit.get("raw_text") or ""
            doi = cit.get("doi") or ""
            text_hash = _sha256(raw_text) if raw_text else ""

            if text_hash:
                cached = await self.db.get_cached_resolution(text_hash)
                if cached is not None:
                    resolved_doi, level = cached
                    work = await self.db.get_work(resolved_doi) if resolved_doi else None
                    results[i] = ResolutionResult(
                        work=work, level=level, source_doi=doi, source_text=raw_text)
                    continue

            if doi:
                existing = await self.db.get_work(doi)
                if existing:
                    if text_hash:
                        await self.db.cache_resolution(text_hash, raw_text, doi, "A")
                    results[i] = ResolutionResult(
                        work=existing, level="A", source_doi=doi, source_text=raw_text)
                    continue

            need_api.append((i, cit))

        if not need_api:
            logger.info("All %d citations resolved from cache", len(citations))
            return [r for r in results if r is not None]

        logger.info(
            "Cache: %d/%d hits, %d need API",
            len(citations) - len(need_api), len(citations), len(need_api),
        )

        # ---- Phase 2: bulk DOI resolution ----
        unique_dois: dict[str, list[int]] = {}  # doi -> [citation indices]
        no_doi_indices: list[int] = []
        for idx, cit in need_api:
            doi = cit.get("doi") or ""
            if doi:
                unique_dois.setdefault(doi, []).append(idx)
            else:
                no_doi_indices.append(idx)

        doi_to_work: dict[str, Work] = {}
        doi_list = list(unique_dois.keys())
        # Batch DOI lookups concurrently
        doi_tasks = []
        for batch_start in range(0, len(doi_list), _DOI_BATCH_SIZE):
            batch = doi_list[batch_start:batch_start + _DOI_BATCH_SIZE]
            doi_tasks.append(self._bulk_fetch_by_dois(batch))
        if doi_tasks:
            doi_results = await asyncio.gather(*doi_tasks, return_exceptions=True)
            for dr in doi_results:
                if isinstance(dr, BaseException):
                    logger.warning("DOI batch fetch failed: %s", dr)
                else:
                    doi_to_work.update(dr)

        if doi_to_work:
            await self.db.upsert_works(list(doi_to_work.values()))

        # Map DOI results to citations
        unresolved_indices: list[int] = list(no_doi_indices)
        for doi, indices in unique_dois.items():
            work = doi_to_work.get(doi)
            for idx in indices:
                cit = citations[idx]
                raw_text = cit.get("raw_text") or ""
                text_hash = _sha256(raw_text) if raw_text else ""
                if work:
                    if text_hash:
                        await self.db.cache_resolution(text_hash, raw_text, work.doi, "A")
                    results[idx] = ResolutionResult(
                        work=work, level="A", source_doi=doi, source_text=raw_text)
                else:
                    unresolved_indices.append(idx)

        logger.info(
            "DOI batch: %d unique DOIs, %d resolved, %d unresolved",
            len(unique_dois), len(doi_to_work),
            len(unresolved_indices),
        )

        # ---- Phase 3: expand referenced_works (depth-1) ----
        all_resolved_works = list(doi_to_work.values())
        ref_works: dict[str, Work] = {}
        if self.expand_references and all_resolved_works:
            ref_works = await self._expand_references_bulk(all_resolved_works)

        # ---- Phase 4: match remaining citations locally ----
        # Build a document-frequency-filtered inverted token index, then
        # for each unresolved bib pick a small candidate set sharing the
        # rarest tokens, and score with rapidfuzz's C-loop `process.extractOne`
        # using `partial_ratio` (clean title aligned within noisy raw_text).
        all_works = {**doi_to_work, **{w.doi: w for w in ref_works.values() if w.doi}}
        works_indexed: list[Work] = [
            w for w in all_works.values()
            if w.title and len(w.title) >= _MIN_TITLE_LEN
        ]
        title_lowers = [w.title.lower() for w in works_indexed]
        title_tokens = [_significant_tokens(t) for t in title_lowers]
        inv: dict[str, list[int]] = {}
        for i, toks in enumerate(title_tokens):
            for tok in toks:
                inv.setdefault(tok, []).append(i)
        max_df = max(2, int(len(works_indexed) * _MAX_DF_RATIO))
        # Drop tokens that appear in too many titles -- they only inflate
        # candidate sets without distinguishing works.
        for tok in [t for t, lst in inv.items() if len(lst) > max_df]:
            del inv[tok]

        cache_rows: list[tuple[str, str, str | None, str]] = []
        for idx in unresolved_indices:
            cit = citations[idx]
            raw_text = cit.get("raw_text") or ""
            text_hash = _sha256(raw_text) if raw_text else ""

            best_work = None
            best_score = 0.0
            if works_indexed and raw_text:
                raw_window = raw_text[:_RAW_WINDOW].lower()
                counts: dict[int, int] = {}
                for tok in _significant_tokens(raw_window):
                    for j in inv.get(tok, ()):
                        counts[j] = counts.get(j, 0) + 1
                # Keep candidates with enough shared rare tokens, capped
                # at top-K by shared count to bound scoring cost.
                kept = [(c, j) for j, c in counts.items() if c >= _MIN_SHARED_TOKENS]
                if kept:
                    kept.sort(reverse=True)
                    cand_idx = [j for _, j in kept[:_MAX_CANDIDATES]]
                    cand_titles = [title_lowers[j] for j in cand_idx]
                    hit = process.extractOne(
                        raw_window, cand_titles,
                        scorer=fuzz.partial_ratio,
                        score_cutoff=self.confidence_threshold,
                    )
                    if hit is not None:
                        _, best_score, k = hit
                        best_work = works_indexed[cand_idx[k]]

            if best_work and best_score >= self.confidence_threshold:
                if text_hash:
                    cache_rows.append((text_hash, raw_text, best_work.doi, "C"))
                results[idx] = ResolutionResult(
                    work=best_work, level="C",
                    source_doi=cit.get("doi", ""), source_text=raw_text)
            else:
                if text_hash:
                    cache_rows.append((text_hash, raw_text, None, "miss"))
                results[idx] = ResolutionResult(
                    work=None, level="miss",
                    source_doi=cit.get("doi", ""), source_text=raw_text)
        await self.db.cache_resolutions_many(cache_rows)

        final = [r if r is not None else ResolutionResult(work=None, level="miss")
                 for r in results]

        if progress_callback:
            for i, r in enumerate(final):
                progress_callback(i + 1, len(citations), r)

        return final

    async def _expand_references_bulk(self, parent_works: list[Work]) -> dict[str, Work]:
        """Bulk-fetch metadata for all referenced_works and store edges."""
        parent_refs: dict[str, list[str]] = {}
        for w in parent_works:
            ref_urls = w.raw.get("referenced_works") or []
            if ref_urls:
                parent_refs[w.doi] = [_extract_openalex_id(u) for u in ref_urls]

        if not parent_refs:
            return {}

        all_oa_ids: set[str] = set()
        for ids in parent_refs.values():
            all_oa_ids.update(ids)

        known = await self.db.known_openalex_ids()
        to_fetch = [oa for oa in all_oa_ids if oa not in known]

        logger.info(
            "Expanding references: %d unique IDs, %d to fetch (%d cached)",
            len(all_oa_ids), len(to_fetch), len(all_oa_ids) - len(to_fetch),
        )

        fetched: dict[str, Work] = {}
        fetch_tasks = []
        for batch_start in range(0, len(to_fetch), _OA_BATCH_SIZE):
            batch = to_fetch[batch_start:batch_start + _OA_BATCH_SIZE]
            fetch_tasks.append(self._bulk_fetch_by_openalex_ids(batch))

        if fetch_tasks:
            batch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for br in batch_results:
                if isinstance(br, BaseException):
                    logger.warning("Reference expansion failed: %s", br)
                    continue
                for w in br:
                    fetched[w.openalex_id] = w

        if fetched:
            await self.db.upsert_works(list(fetched.values()))

        # Build edges
        oa_to_doi = {w.openalex_id: w.doi for w in fetched.values()}
        for oa_id in all_oa_ids - set(to_fetch):
            existing = await self.db.get_work_by_openalex(oa_id)
            if existing:
                oa_to_doi[existing.openalex_id] = existing.doi

        for parent_doi, child_oa_ids in parent_refs.items():
            child_dois = [oa_to_doi[oa] for oa in child_oa_ids if oa in oa_to_doi]
            if child_dois:
                await self.db.add_edges(parent_doi, child_dois)

        return fetched
