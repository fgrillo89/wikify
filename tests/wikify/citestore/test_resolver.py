"""Tests for AsyncResolver with mocked HTTP."""

from __future__ import annotations

import httpx
import pytest

from wikify.citations.db import DatabaseManager
from wikify.citations.resolver import AsyncResolver, parse_openalex_work

from .conftest import SAMPLE_OPENALEX_CHILD, SAMPLE_OPENALEX_WORK


def _make_transport(routes: dict[str, object]) -> httpx.MockTransport:
    """Build a MockTransport that returns canned responses based on URL content."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, response_data in routes.items():
            if pattern in url:
                return httpx.Response(200, json=response_data)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


# ---- parse_openalex_work ----

def test_parse_openalex_work(sample_work_json):
    w = parse_openalex_work(sample_work_json)
    assert w.doi == "10.1038/s41586-020-2649-2"
    assert w.openalex_id == "W2741809807"
    assert w.title == "Array programming with NumPy"
    assert w.year == 2020
    assert w.journal == "Nature"
    assert len(w.authors) == 2
    assert w.authors[0] == "Charles R. Harris"
    assert w.volume == "585"
    assert w.first_page == "357"
    assert w.last_page == "362"
    assert w.bibtex.startswith("@article{")


# ---- Level A: DOI resolution ----

@pytest.mark.asyncio
async def test_resolve_by_doi(tmp_path):
    transport = _make_transport({
        "filter=doi": {"results": [SAMPLE_OPENALEX_WORK]},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            results = await resolver.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris et al 2020 NumPy"},
            ])
        finally:
            await resolver.close()

    assert len(results) == 1
    assert results[0].level == "A"
    assert results[0].work is not None
    assert results[0].work.title == "Array programming with NumPy"


# ---- Local fuzzy matching (Phase 4) ----

@pytest.mark.asyncio
async def test_local_fuzzy_match(tmp_path):
    """Citation without DOI should match locally against DOI-resolved works."""
    transport = _make_transport({
        "filter=doi": {"results": [SAMPLE_OPENALEX_WORK]},
        "filter=openalex": {"results": []},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            results = await resolver.resolve_batch([
                # First citation has DOI -- resolved via bulk fetch
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris et al 2020"},
                # Second citation has no DOI -- should match locally
                {"raw_text": "Array programming with NumPy"},
            ])
        finally:
            await resolver.close()

    assert len(results) == 2
    assert results[0].level == "A"
    assert results[1].work is not None
    assert results[1].level == "C"


@pytest.mark.asyncio
async def test_local_fuzzy_below_threshold(tmp_path):
    """Low similarity against resolved works should produce a miss."""
    transport = _make_transport({
        "filter=doi": {"results": [SAMPLE_OPENALEX_WORK]},
        "filter=openalex": {"results": []},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            results = await resolver.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris 2020"},
                {"raw_text": "Completely unrelated text about cooking recipes"},
            ])
        finally:
            await resolver.close()

    assert results[1].level == "miss"
    assert results[1].work is None


# ---- Resumability ----

@pytest.mark.asyncio
async def test_resumability_skips_cached(tmp_path):
    """Second run should hit string_cache, not make HTTP calls."""
    call_count = 0

    def counting_transport(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_WORK]})

    async with DatabaseManager(tmp_path / "test.db") as db:
        # First run
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        mock_transport = httpx.MockTransport(counting_transport)
        resolver._client = httpx.AsyncClient(transport=mock_transport)
        try:
            await resolver.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris et al 2020"},
            ])
        finally:
            await resolver.close()

        first_count = call_count

        # Second run - should use cache
        resolver2 = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver2._client = httpx.AsyncClient(transport=mock_transport)
        try:
            results = await resolver2.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris et al 2020"},
            ])
        finally:
            await resolver2.close()

    assert call_count == first_count  # no new HTTP calls
    assert results[0].work is not None


# ---- Reference expansion ----

@pytest.mark.asyncio
async def test_expand_references(tmp_path):
    """Depth-1 expansion should bulk-fetch referenced_works and store edges."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "filter=doi" in url:
            return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_WORK]})
        if "filter=openalex" in url:
            return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_CHILD]})
        return httpx.Response(200, json={"results": []})

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=True,
        )
        resolver._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await resolver.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "Harris 2020"},
            ])
        finally:
            await resolver.close()

        # Should have the parent + at least one child
        all_works = await db.get_all_works()
        assert len(all_works) >= 2

        edges = await db.get_all_edges()
        assert len(edges) >= 1
        parent_dois = {e[0] for e in edges}
        assert "10.1038/s41586-020-2649-2" in parent_dois


# ---- Regression coverage for the optimised Phase 4 / DOI canonicalisation ----

@pytest.mark.asyncio
async def test_short_title_local_fuzzy_match(tmp_path):
    """Short but accurate titles must still participate in Phase 4.

    Earlier the matcher dropped any title shorter than _MIN_TITLE_LEN
    chars from the inverted index, so a no-DOI bib citing a short
    corpus title returned miss even though the DOI-resolved seed was
    in doi_to_work.
    """
    short_work = {
        **SAMPLE_OPENALEX_WORK,
        "id": "https://openalex.org/W999",
        "doi": "https://doi.org/10.1000/short",
        "title": "Memristor Synapse",
    }
    transport = _make_transport({
        "filter=doi": {"results": [short_work]},
        "filter=openalex": {"results": []},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            results = await resolver.resolve_batch([
                {"doi": "10.1000/short", "raw_text": "seed"},
                {"raw_text": "Harris C.R. Memristor Synapse. Nature 2020;1:2."},
            ])
        finally:
            await resolver.close()

    assert results[1].work is not None, "short title should match in Phase 4"
    assert results[1].work.doi == "10.1000/short"
    assert results[1].level == "C"


@pytest.mark.asyncio
async def test_cached_referenced_work_participates_in_fuzzy(tmp_path):
    """Re-running the resolver after a previous run cached the
    referenced_works must still let no-DOI bibs fuzzy-match those
    cached refs. Earlier _expand_references_bulk returned only newly
    fetched works, so the cached references were absent from
    Phase 4's title index.
    """
    transport = _make_transport({
        "filter=doi": {"results": [SAMPLE_OPENALEX_WORK]},
        "filter=openalex": {"results": [SAMPLE_OPENALEX_CHILD]},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        # First run: fetches and caches the parent + child.
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=True,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            await resolver.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "seed"},
            ])
        finally:
            await resolver.close()

        # Second run with a fresh resolver: parent comes from cache,
        # so the openalex batch returns []; the child must still be
        # available in Phase 4's title index for the no-DOI bib to hit.
        cached_only_transport = _make_transport({
            "filter=doi": {"results": [SAMPLE_OPENALEX_WORK]},
            "filter=openalex": {"results": []},
        })
        resolver2 = AsyncResolver(
            db, email="test@example.com", expand_references=True,
        )
        resolver2._client = httpx.AsyncClient(transport=cached_only_transport)
        try:
            results = await resolver2.resolve_batch([
                {"doi": "10.1038/s41586-020-2649-2", "raw_text": "fresh seed"},
                {"raw_text": "J. Doe. A Child Reference Paper. JoT 10, 1-10 (2015)."},
            ])
        finally:
            await resolver2.close()

    assert results[1].work is not None, (
        "cached referenced_work should remain in the Phase 4 title index"
    )
    assert results[1].work.doi == "10.1000/child-work"


@pytest.mark.asyncio
async def test_doi_canonicalisation_resolves_as_level_a(tmp_path):
    """A citation with mixed-case / URL-prefixed DOI must still hit
    level A when OpenAlex returns the canonical lowercased form.
    Earlier the dict lookup keyed by the citation's raw DOI string
    missed when OpenAlex normalised the case."""
    work = {
        **SAMPLE_OPENALEX_WORK,
        "id": "https://openalex.org/W777",
        "doi": "https://doi.org/10.1000/case",  # canonical lowercase
        "title": "Case Sensitivity Study Title",
    }
    transport = _make_transport({
        "filter=doi": {"results": [work]},
        "filter=openalex": {"results": []},
    })

    async with DatabaseManager(tmp_path / "test.db") as db:
        resolver = AsyncResolver(
            db, email="test@example.com", expand_references=False,
        )
        resolver._client = httpx.AsyncClient(transport=transport)
        try:
            results = await resolver.resolve_batch([
                {"doi": "https://doi.org/10.1000/CASE", "raw_text": "x"},
                {"doi": "10.1000/Case", "raw_text": "y"},
                {"doi": "doi:10.1000/case", "raw_text": "z"},
            ])
        finally:
            await resolver.close()

    for r in results:
        assert r.work is not None, f"expected level-A hit, got miss for {r.source_doi}"
        assert r.level == "A"
        assert r.work.doi == "10.1000/case"


@pytest.mark.asyncio
async def test_cache_resolutions_many_round_trip(tmp_path):
    """Bulk cache writes must round-trip through string_cache the same
    way per-row writes did (single transaction, atomic commit, all
    rows readable on the next call)."""
    rows = [
        ("h1", "raw1", "10.1/a", "C"),
        ("h2", "raw2", None, "miss"),
        ("h3", "raw3", "10.1/b", "A"),
    ]
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.cache_resolutions_many(rows)
        assert await db.get_cached_resolution("h1") == ("10.1/a", "C")
        assert await db.get_cached_resolution("h2") == (None, "miss")
        assert await db.get_cached_resolution("h3") == ("10.1/b", "A")
        # Empty list is a no-op (no transaction error).
        await db.cache_resolutions_many([])
        # Re-insert with same hashes overrides the row (INSERT OR REPLACE).
        await db.cache_resolutions_many([("h1", "raw1", "10.1/x", "A")])
        assert await db.get_cached_resolution("h1") == ("10.1/x", "A")
