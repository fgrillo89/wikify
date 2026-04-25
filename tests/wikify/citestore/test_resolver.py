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
