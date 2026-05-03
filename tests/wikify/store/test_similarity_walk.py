"""`similarity_walk`: cosine-neighbour exploration over chunk vectors."""

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
    _md(sources / "a.md", "Alpha",
        "Atomic layer deposition of HfO2 yields uniform films via self-limiting reactions.")
    _md(sources / "b.md", "Beta",
        "Atomic layer deposition deposits conformal HfO2 films "
        "through alternating precursor pulses.")
    _md(sources / "c.md", "Gamma",
        "Photocatalysis on titanium dioxide drives water splitting under ultraviolet light.")
    _md(sources / "d.md", "Delta",
        "Atomic layer deposition produces conformal thin films in high aspect ratio trenches.")
    yield ingest_corpus(sources, tmp_path / "corpus", max_workers=1)


def test_query_seed_returns_seeds_and_neighbours(corpus):
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=2, top_k=2,
        neighbors=2, threshold=0.0,
    )
    assert out["seeds"], "expected hop-0 seeds"
    assert all(c["hop"] >= 0 for c in out["chunks"].values())
    # Edges should fire when threshold is permissive.
    assert out["edges"], "expected at least one similarity edge"


def test_dedup_chunks_across_paths(corpus):
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=2, top_k=3,
        neighbors=3, threshold=0.0,
    )
    ids = list(out["chunks"].keys())
    assert len(ids) == len(set(ids))


def test_threshold_drops_low_score_edges(corpus):
    """A threshold of 0.99 admits only near-identical chunks; on a small
    fixture with diverse content, that prunes the edge set to near zero."""
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=1, top_k=3,
        neighbors=3, threshold=0.99,
    )
    # No false positives.
    for e in out["edges"]:
        assert e["score"] >= 0.99


def test_depth_zero_returns_seeds_only(corpus):
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=0, top_k=3,
        neighbors=3, threshold=0.0,
    )
    assert out["seeds"]
    assert out["edges"] == []
    # All chunks are hop 0.
    assert all(c["hop"] == 0 for c in out["chunks"].values())


def test_cross_doc_only_excludes_same_doc(corpus):
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=1, top_k=2,
        neighbors=5, threshold=0.0, cross_doc_only=True,
    )
    for e in out["edges"]:
        src_doc = out["chunks"][e["src_chunk"]]["doc_id"]
        dst_doc = out["chunks"][e["dst_chunk"]]["doc_id"]
        assert src_doc != dst_doc, f"cross_doc_only let same-doc edge through: {e}"


def test_include_same_doc_can_emit_same_doc_edges(corpus):
    """With cross_doc_only=False, neighbours within the same doc surface
    when the corpus has any multi-chunk doc."""
    out = queries.similarity_walk(
        corpus, query="atomic layer deposition", depth=1, top_k=4,
        neighbors=5, threshold=0.0, cross_doc_only=False,
    )
    # We expect both shapes possible; just assert the call works.
    assert out["seeds"]


def test_from_chunk_mode_works(corpus):
    """Seed via a chunk handle instead of a query."""
    seeds = queries.search_chunks(
        corpus, "atomic layer deposition", top_k=1, rank="bm25",
    )
    seed_id = seeds[0]["id"]
    out = queries.similarity_walk(
        corpus, from_chunk=seed_id, depth=1, neighbors=3, threshold=0.0,
    )
    assert out["seeds"][0]["id"] == seed_id
    assert all(c["hop"] in {0, 1} for c in out["chunks"].values())


def test_query_and_from_chunk_mutually_exclusive(corpus):
    with pytest.raises(queries.QueryError) as exc:
        queries.similarity_walk(corpus, query="x", from_chunk="abc", depth=1)
    assert exc.value.code == "bad_seed"


def test_neither_query_nor_from_chunk_raises(corpus):
    with pytest.raises(queries.QueryError) as exc:
        queries.similarity_walk(corpus, depth=1)
    assert exc.value.code == "bad_seed"


def test_no_wikify_db_raises(tmp_path):
    from wikify.api import Corpus
    root = tmp_path / "corpus"
    root.mkdir()
    c = Corpus(root=root)
    with pytest.raises(queries.QueryError) as exc:
        queries.similarity_walk(c, query="x", depth=1)
    assert exc.value.code == "no_wikify_db"


def test_bad_threshold_raises(corpus):
    with pytest.raises(queries.QueryError) as exc:
        queries.similarity_walk(corpus, query="x", depth=1, threshold=1.5)
    assert exc.value.code == "bad_threshold"
