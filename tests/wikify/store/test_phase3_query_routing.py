"""bm25 / hybrid rank routing through the SQLite store.

The SQLite store is the authoritative query backend; these tests
verify the lexical ranks work end-to-end through the public
``queries.find`` surface.
"""

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
def small_corpus(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    _md(sources / "a.md", "Alpha title",
        "Photocatalysis on titanium dioxide drives water splitting under UV.")
    _md(sources / "b.md", "Beta title",
        "Atomic layer deposition produces conformal thin films via precursor chemistry.")
    paths = ingest_corpus(sources, tmp_path / "corpus", max_workers=1)
    yield paths


def test_bm25_rank_returns_chunk_hits(small_corpus):
    out = queries.find(
        small_corpus, query="titanium dioxide", by="chunk", rank="bm25", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert out["rows"], "expected at least one bm25 hit"
    assert out["rows"][0]["doc_id"]


def test_hybrid_rank_smoke(small_corpus):
    out = queries.find(
        small_corpus, query="atomic layer deposition", by="chunk",
        rank="hybrid", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert out["rows"]


def test_bm25_paper_aggregation(small_corpus):
    out = queries.find(
        small_corpus, query="atomic layer deposition", by="paper",
        rank="bm25", top_k=5,
    )
    assert out["kind"] == "papers"
    assert out["rows"]


def test_semantic_rank_default_path(small_corpus):
    out = queries.find(
        small_corpus, query="photocatalysis", by="chunk", rank="semantic", top_k=3,
    )
    assert out["kind"] == "chunks"


def test_lexical_rank_without_wikify_db_raises(tmp_path):
    """No wikify.db -> bm25/hybrid surfaces a clear error."""
    from wikify.api import Corpus

    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    corpus = Corpus(root=corpus_root)
    with pytest.raises(queries.QueryError) as exc:
        queries.find(corpus, query="x", by="chunk", rank="bm25", top_k=5)
    assert exc.value.code == "no_wikify_db"
