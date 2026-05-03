"""--in-doc scoping for `find` and the new `citation-walk` traversal."""

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
        "Photocatalysis on titanium dioxide drives water splitting under ultraviolet light.")
    _md(sources / "c.md", "Gamma",
        "Atomic layer deposition produces conformal thin films in high aspect ratio trenches.")
    yield ingest_corpus(sources, tmp_path / "corpus", max_workers=1)


# ---------------------------------------------------------------- --in-doc


def test_in_doc_bm25_scopes_to_one_doc(corpus):
    """BM25 hits scoped to one doc only return chunks from that doc."""
    all_hits = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="bm25", top_k=5,
    )["rows"]
    assert all_hits
    target_doc = all_hits[0]["doc_id"]

    scoped = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="bm25",
        top_k=5, in_doc=target_doc,
    )["rows"]
    assert scoped
    assert all(h["doc_id"] == target_doc for h in scoped)


def test_in_doc_semantic_scopes_via_post_filter(corpus):
    all_hits = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="semantic", top_k=5,
    )["rows"]
    target_doc = all_hits[0]["doc_id"]
    scoped = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="semantic",
        top_k=5, in_doc=target_doc,
    )["rows"]
    assert scoped
    assert all(h["doc_id"] == target_doc for h in scoped)


def test_in_doc_all_modes_keeps_via_tags(corpus):
    all_hits = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="bm25", top_k=3,
    )["rows"]
    target_doc = all_hits[0]["doc_id"]
    scoped = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="all",
        top_k=5, in_doc=target_doc,
    )["rows"]
    assert scoped
    assert all(h["doc_id"] == target_doc for h in scoped)
    assert all("modes" in h for h in scoped)


def test_in_doc_unmatched_doc_returns_empty(corpus):
    """Querying for ALD inside a doc that is purely about photocatalysis returns []."""
    pcat = next(
        (h for h in queries.find(
            corpus, query="photocatalysis", by="chunk", rank="bm25", top_k=3,
        )["rows"]),
        None,
    )
    assert pcat is not None
    out = queries.find(
        corpus, query="atomic layer deposition", by="chunk", rank="bm25",
        top_k=5, in_doc=pcat["doc_id"],
    )["rows"]
    # The pcat doc body contains "photocatalysis" not "atomic layer deposition";
    # bm25 with AND default returns no hits scoped to that doc.
    assert out == []


# ---------------------------------------------------------------- citation-walk


def test_citation_walk_returns_seed_shape(corpus):
    out = queries.citation_walk(
        corpus, query="atomic layer deposition", depth=0, top_k=3,
    )
    assert "seeds" in out and "edges" in out and "chunks" in out
    assert out["seeds"], "expected hop-0 seeds"
    assert all(c["hop"] == 0 for c in out["seeds"])
    # depth=0 means no edges fire.
    assert out["edges"] == []


def test_citation_walk_handles_zero_resolved_cites(corpus):
    """In-fixture corpora rarely have in-corpus cross-citations; the walk
    still produces seeds and just terminates at hop 0."""
    out = queries.citation_walk(
        corpus, query="atomic layer deposition", depth=2, top_k=3,
    )
    assert out["seeds"]
    # depth=2 with no resolved cites: edges may be empty.
    for c in out["chunks"].values():
        assert c["hop"] >= 0


def test_citation_walk_rejects_negative_depth(corpus):
    with pytest.raises(queries.QueryError) as exc:
        queries.citation_walk(corpus, query="x", depth=-1, top_k=3)
    assert exc.value.code == "bad_depth"


def test_citation_walk_no_wikify_db_raises(tmp_path):
    from wikify.api import Corpus
    root = tmp_path / "corpus"
    root.mkdir()
    c = Corpus(root=root)
    with pytest.raises(queries.QueryError) as exc:
        queries.citation_walk(c, query="x", depth=1, top_k=3)
    assert exc.value.code == "no_wikify_db"
