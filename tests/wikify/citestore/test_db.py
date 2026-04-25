"""Tests for DatabaseManager."""

from __future__ import annotations

import pytest

from wikify.citations.db import DatabaseManager
from wikify.citations.models import Work


def _make_work(**overrides) -> Work:
    defaults = dict(
        doi="10.1234/test",
        openalex_id="W999",
        title="Test Paper",
        year=2024,
        journal="Test Journal",
        authors=["Alice", "Bob"],
        volume="1",
        issue="2",
        first_page="10",
        last_page="20",
        publisher="Test Pub",
        cited_by_count=42,
        work_type="journal-article",
        bibtex="@article{test, title={Test}}",
        raw={"id": "https://openalex.org/W999"},
    )
    defaults.update(overrides)
    return Work(**defaults)


@pytest.mark.asyncio
async def test_init_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    async with DatabaseManager(db_path) as db:
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r["name"] for r in await cursor.fetchall()}
    assert "works" in tables
    assert "string_cache" in tables
    assert "citation_edges" in tables


@pytest.mark.asyncio
async def test_upsert_and_get_work(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        w = _make_work()
        await db.upsert_work(w)
        got = await db.get_work("10.1234/test")
        assert got is not None
        assert got.title == "Test Paper"
        assert got.authors == ["Alice", "Bob"]
        assert got.year == 2024


@pytest.mark.asyncio
async def test_get_work_by_openalex(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.upsert_work(_make_work())
        got = await db.get_work_by_openalex("W999")
        assert got is not None
        assert got.doi == "10.1234/test"


@pytest.mark.asyncio
async def test_get_work_miss(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        assert await db.get_work("nonexistent") is None


@pytest.mark.asyncio
async def test_upsert_works_batch(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        works = [
            _make_work(doi=f"10.1234/{i}", openalex_id=f"W{i}")
            for i in range(5)
        ]
        await db.upsert_works(works)
        all_works = await db.get_all_works()
        assert len(all_works) == 5


@pytest.mark.asyncio
async def test_upsert_works_empty(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.upsert_works([])  # should not error


@pytest.mark.asyncio
async def test_string_cache_round_trip(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.cache_resolution("abc123", "some raw text", "10.1234/test", "A")
        result = await db.get_cached_resolution("abc123")
        assert result == ("10.1234/test", "A")


@pytest.mark.asyncio
async def test_string_cache_miss_stored(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.cache_resolution("xyz789", "unresolvable text", None, "miss")
        result = await db.get_cached_resolution("xyz789")
        assert result == (None, "miss")


@pytest.mark.asyncio
async def test_string_cache_not_found(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        assert await db.get_cached_resolution("nonexistent") is None


@pytest.mark.asyncio
async def test_edges(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.add_edges("10.1234/parent", ["10.1234/c1", "10.1234/c2"])
        edges = await db.get_all_edges()
        assert len(edges) == 2
        assert ("10.1234/parent", "10.1234/c1") in edges


@pytest.mark.asyncio
async def test_edges_dedup(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.add_edges("10.1234/p", ["10.1234/c1"])
        await db.add_edges("10.1234/p", ["10.1234/c1"])  # duplicate
        edges = await db.get_all_edges()
        assert len(edges) == 1


@pytest.mark.asyncio
async def test_known_openalex_ids(tmp_path):
    async with DatabaseManager(tmp_path / "test.db") as db:
        await db.upsert_works([
            _make_work(doi="10.1234/a", openalex_id="W1"),
            _make_work(doi="10.1234/b", openalex_id="W2"),
            _make_work(doi="10.1234/c", openalex_id=""),
        ])
        known = await db.known_openalex_ids()
        assert known == {"W1", "W2"}
