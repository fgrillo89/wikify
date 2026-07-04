"""--rank all: semantic + bm25 + text fan-out with mode tags."""

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
def corpus(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    _md(sources / "a.md", "Atomic layer deposition fundamentals",
        "Growth per cycle (GPC) measures how much film is deposited per ALD cycle.")
    _md(sources / "b.md", "Beta paper",
        "Photocatalysis on titanium dioxide drives water splitting.")
    yield ingest_corpus(sources, tmp_path / "corpus", max_workers=1)


def test_rank_all_returns_modes_field(corpus):
    out = queries.find(
        corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert out["rows"], "expected hits"
    for row in out["rows"]:
        assert "modes" in row
        assert all(m in {"semantic", "bm25", "text"} for m in row["modes"])


def test_rank_all_consensus_rises(corpus):
    """Chunks matched by all three modes should rank ahead of single-mode hits."""
    out = queries.find(
        corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
    )
    rows = out["rows"]
    # Find a row where all three modes agree.
    all_three = {"semantic", "bm25", "text"}
    full = next((r for r in rows if set(r["modes"]) == all_three), None)
    assert full is not None, (
        f"expected at least one sbt consensus hit; got {[r['modes'] for r in rows]}"
    )
    # And it should sit before any single-mode-only row.
    full_idx = rows.index(full)
    single_idx = next(
        (i for i, r in enumerate(rows) if len(r["modes"]) == 1), len(rows),
    )
    assert full_idx <= single_idx


def test_rank_all_dedupes(corpus):
    out = queries.find(
        corpus, query="GPC", by="chunk", rank="all", top_k=10,
    )
    ids = [r["id"] for r in out["rows"]]
    assert len(ids) == len(set(ids))


def test_rank_all_top_k_is_a_total_cap(corpus):
    out = queries.find(
        corpus, query="growth", by="chunk", rank="all", top_k=3,
    )
    assert len(out["rows"]) <= 3


def test_rank_all_tolerates_fts5_syntax_error(corpus):
    """Hyphenated query that BM25 mis-parses still returns hits via the
    semantic + text channels."""
    out = queries.find(
        corpus, query="self-limiting", by="chunk", rank="all", top_k=5,
    )
    assert out["kind"] == "chunks"
    # No assertion on row count — fixture text doesn't contain
    # "self-limiting", but the call must not raise.


def test_rank_all_with_no_wikify_db_raises(tmp_path):
    from wikify.api import Corpus

    root = tmp_path / "corpus"
    root.mkdir()
    corpus = Corpus(root=root)
    with pytest.raises(queries.QueryError) as exc:
        queries.find(corpus, query="x", by="chunk", rank="all", top_k=5)
    assert exc.value.code == "no_wikify_db"


# --------------------------------------------------------------- strict_semantic


def _break_semantic(monkeypatch) -> None:
    """Make the vector-index search raise, simulating a broken embedder path."""
    from wikify.corpus.store import Store

    class _BadIndex:
        def search(self, *a, **k):
            raise RuntimeError("vector index unavailable")

    def _fake_vector_index(self, space_id, node_type="chunk"):
        return _BadIndex()

    monkeypatch.setattr(Store, "vector_index", _fake_vector_index)


def test_strict_semantic_healthy_does_not_raise(corpus):
    """A working embedder: strict_semantic must NOT raise, and the semantic
    channel participates (no false positive)."""
    out = queries.find(
        corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
        strict_semantic=True,
    )
    assert out["rows"]
    assert any("semantic" in r["modes"] for r in out["rows"])


def test_strict_semantic_raises_when_semantic_fails(corpus, monkeypatch):
    """strict_semantic surfaces a semantic-mode failure loudly rather than
    silently degrading to bm25+text."""
    _break_semantic(monkeypatch)
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
            strict_semantic=True,
        )
    assert exc.value.code == "semantic_search_failed"


def test_default_tolerates_semantic_failure(corpus, monkeypatch):
    """Default (strict_semantic off): a semantic failure is still swallowed so
    interactive search keeps returning lexical hits -- unchanged behavior."""
    _break_semantic(monkeypatch)
    out = queries.find(
        corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert all("semantic" not in r["modes"] for r in out["rows"])


def test_strict_semantic_raises_when_query_embedding_fails(corpus, monkeypatch):
    """A broken embedder that fails at QUERY-EMBED time (before the vector
    search, so outside _safe_mode) is still normalized to a structured
    QueryError under strict -- not a raw exception."""
    import wikify.embedding as emb

    def _boom_embedder(*a, **k):
        raise RuntimeError("onnxruntime/fastembed unavailable")

    monkeypatch.setattr(emb, "embedder_for", _boom_embedder)
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
            strict_semantic=True,
        )
    assert exc.value.code == "semantic_search_failed"


def test_default_tolerates_query_embedding_failure(corpus, monkeypatch):
    """Without strict, a query-embed failure degrades to lexical channels
    instead of raising -- unchanged interactive-search behavior."""
    import wikify.embedding as emb

    def _boom_embedder(*a, **k):
        raise RuntimeError("onnxruntime/fastembed unavailable")

    monkeypatch.setattr(emb, "embedder_for", _boom_embedder)
    out = queries.find(
        corpus, query="growth per cycle", by="chunk", rank="all", top_k=5,
    )
    assert out["kind"] == "chunks"
    assert all("semantic" not in r["modes"] for r in out["rows"])


def test_safe_mode_reraise_true_raises():
    def boom():
        raise RuntimeError("x")

    with pytest.raises(queries.QueryError) as exc:
        queries._safe_mode("semantic", boom, reraise=True)
    assert exc.value.code == "semantic_search_failed"


def test_safe_mode_reraise_false_swallows():
    def boom():
        raise RuntimeError("x")

    assert queries._safe_mode("semantic", boom, reraise=False) == []
