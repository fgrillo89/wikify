"""Phase 3 acceptance: WIKIFY_QUERY_BACKEND routing + bm25/hybrid ranks."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.corpus import queries
from wikify.ingest.pipeline import ingest_corpus

_FILLER = " ".join(["word"] * 30)


def _md(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body} {_FILLER}\n", encoding="utf-8")


@pytest.fixture
def small_corpus(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    _md(sources / "a.md", "Alpha title",
        "Photocatalysis on titanium dioxide drives water splitting under UV.")
    _md(sources / "b.md", "Beta title",
        "Atomic layer deposition produces conformal thin films via precursor chemistry.")
    paths = ingest_corpus(sources, tmp_path / "corpus", max_workers=1)
    yield paths


def test_bm25_rank_returns_hits_under_sqlite_backend(small_corpus, monkeypatch):
    monkeypatch.setenv("WIKIFY_QUERY_BACKEND", "sqlite")
    out = queries.find(
        small_corpus, query="titanium dioxide", by="chunk", rank="bm25", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert out["rows"], "expected at least one bm25 hit"
    assert out["rows"][0]["doc_id"]


def test_bm25_rank_rejected_under_legacy_backend(small_corpus, monkeypatch):
    monkeypatch.setenv("WIKIFY_QUERY_BACKEND", "legacy")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(small_corpus, query="x", by="chunk", rank="bm25", top_k=5)
    assert exc.value.code == "backend_required"


def test_hybrid_rank_smoke(small_corpus, monkeypatch):
    monkeypatch.setenv("WIKIFY_QUERY_BACKEND", "sqlite")
    out = queries.find(
        small_corpus, query="atomic layer deposition", by="chunk",
        rank="hybrid", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert out["rows"]


def test_unknown_backend_raises(monkeypatch):
    from wikify.corpus.store.routing import query_backend
    monkeypatch.setenv("WIKIFY_QUERY_BACKEND", "duckdb")
    with pytest.raises(ValueError):
        query_backend()


def test_bm25_paper_aggregation(small_corpus, monkeypatch):
    monkeypatch.setenv("WIKIFY_QUERY_BACKEND", "sqlite")
    out = queries.find(
        small_corpus, query="atomic layer deposition", by="paper",
        rank="bm25", top_k=5,
    )
    assert out["kind"] == "papers"
    assert out["rows"]


def test_default_legacy_unchanged_for_semantic(small_corpus, monkeypatch):
    monkeypatch.delenv("WIKIFY_QUERY_BACKEND", raising=False)
    out = queries.find(
        small_corpus, query="photocatalysis", by="chunk", rank="semantic", top_k=3,
    )
    assert out["kind"] == "chunks"
