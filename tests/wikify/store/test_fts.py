"""FTS5 BM25 + RRF tests."""

from __future__ import annotations

import numpy as np

from wikify.corpus.store import Store
from wikify.corpus.store.fts import rrf_fuse
from wikify.models import Chunk, Document


def _docs(s: Store) -> None:
    s.upsert_document(Document(
        id="d1", source_path="x", kind="md", title="Photocatalysis fundamentals",
        metadata={}, markdown_path="m", image_dir="i",
        abstract="Titanium dioxide drives photocatalytic water splitting.",
    ))
    s.upsert_document(Document(
        id="d2", source_path="x", kind="md", title="Atomic layer deposition overview",
        metadata={}, markdown_path="m", image_dir="i",
        abstract="Self-limiting precursor chemistry produces conformal thin films.",
    ))
    s.upsert_chunks([
        Chunk(id="d1/c0", doc_id="d1", ord=0,
              text="Titanium dioxide nanoparticles drive water splitting under UV light.",
              char_span=(0, 1), section_path=[]),
        Chunk(id="d1/c1", doc_id="d1", ord=1,
              text="Surface defects affect charge carrier lifetime.",
              char_span=(0, 1), section_path=[]),
        Chunk(id="d2/c0", doc_id="d2", ord=0,
              text="Atomic layer deposition is a thin-film deposition technique.",
              char_span=(0, 1), section_path=[]),
    ])
    s.fts_rebuild()


def test_bm25_chunk_search_returns_relevant_hit():
    s = Store(":memory:")
    _docs(s)
    hits = s.search_chunks_bm25("titanium dioxide", top_k=5)
    assert hits
    assert hits[0][0] == "d1/c0"


def test_bm25_document_search_weights_title():
    s = Store(":memory:")
    _docs(s)
    hits = s.search_documents_bm25("atomic layer deposition", top_k=5)
    assert hits and hits[0][0] == "d2"


def test_rrf_fuse_is_deterministic_and_id_stable():
    a = [("c1", -3.0), ("c2", -2.0), ("c3", -1.0)]
    b = [("c2", 0.9), ("c3", 0.8), ("c4", 0.5)]
    out1 = rrf_fuse([a, b], k=60, top_k=4)
    out2 = rrf_fuse([a, b], k=60, top_k=4)
    assert out1 == out2
    ids = [it for it, _ in out1]
    # c2 appears highest in both rankings -> wins.
    assert ids[0] == "c2"


def test_hybrid_search_falls_back_to_bm25_only_without_vec():
    s = Store(":memory:")
    _docs(s)
    out = s.search_hybrid("titanium dioxide", query_vec=None, top_k=3)
    assert out and out[0][0] in {"d1/c0", "d1/c1"}


def test_hybrid_search_with_vector():
    s = Store(":memory:")
    _docs(s)
    s.upsert_embedding_space("test", "hash", "synthetic", 8)
    rng = np.random.default_rng(42)
    items = []
    for cid in ("d1/c0", "d1/c1", "d2/c0"):
        v = rng.normal(size=8).astype("float32")
        items.append(("chunk", cid, v))
    s.upsert_embeddings("test", items)
    qv = rng.normal(size=8).astype("float32")
    out = s.search_hybrid("titanium dioxide", query_vec=qv, space_id="test", top_k=3, pool=10)
    assert out
