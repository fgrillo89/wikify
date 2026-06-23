"""F14 regression: ``find(by="chunk", rank="pagerank")`` returns CHUNK rows.

The P5 gap-explorer ranks residual *chunks* by PageRank via
``corpus_find(query="", by="chunk", rank="pagerank", top_k=N)``. Previously the
empty-query graph-metric branch returned document rows regardless of ``by``,
so the coverage driver silently degraded to doc-level granularity. ``by="chunk"``
must now project the document metric onto chunks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.corpus import queries
from wikify.corpus.chunks import all_chunks
from wikify.ingest.pipeline import ingest_corpus

_FILLER = " ".join(["word"] * 30)


def _md(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body} {_FILLER}\n", encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    _md(sources / "a.md", "Alpha",
        "Atomic layer deposition of HfO2 yields uniform films via self-limiting reactions.")
    _md(sources / "b.md", "Beta",
        "Memristors exhibit resistive switching through conductive filament formation.")
    _md(sources / "c.md", "Gamma",
        "Neuromorphic computing emulates synaptic plasticity with analog devices.")
    yield ingest_corpus(sources, tmp_path / "corpus", max_workers=1)


def test_by_chunk_pagerank_returns_chunks(corpus):
    result = queries.find(corpus, query="", by="chunk", rank="pagerank", top_k=5)
    assert result["kind"] == "chunks", result["kind"]
    assert result["rows"], "expected at least one chunk row"
    # Every row must be a chunk (has id + doc_id), not a document row.
    for r in result["rows"]:
        assert "id" in r and "doc_id" in r
    # The ids must be real chunk ids, not document ids.
    chunk_ids = {c.id for c in all_chunks(corpus)}
    assert all(r["id"] in chunk_ids for r in result["rows"])


def test_by_chunk_pagerank_respects_top_k(corpus):
    result = queries.find(corpus, query="", by="chunk", rank="pagerank", top_k=2)
    assert result["kind"] == "chunks"
    assert len(result["rows"]) <= 2


def test_by_paper_pagerank_still_returns_docs(corpus):
    """The document-ranking path is unchanged for by='paper'."""
    result = queries.find(corpus, query="", by="paper", rank="pagerank", top_k=3)
    assert result["kind"] == "docs"
