"""Test that blockwise graph build produces identical edges to dense build."""

import numpy as np

from wikify.ingest.config import KNN_K, STRONG_COS
from wikify.ingest.corpus_graph import build_corpus_graph
from wikify.models import Chunk, Document
from wikify.store.vectors import VectorStore


def _make_corpus(n_docs: int, chunks_per_doc: int, dim: int, seed: int = 42):
    """Build a small deterministic corpus for graph comparison."""
    rng = np.random.RandomState(seed)
    docs = []
    chunks = []
    vecs = []
    ids = []
    for d in range(n_docs):
        doc_id = f"doc_{d:03d}"
        docs.append(Document(
            id=doc_id, source_path=f"/src/{doc_id}.md", kind="md",
            title=f"Doc {d}", metadata={}, markdown_path="", image_dir="",
        ))
        for c in range(chunks_per_doc):
            cid = f"{doc_id}__c{c:04d}__abc"
            chunks.append(Chunk(
                id=cid, doc_id=doc_id, ord=c, text=f"chunk {c}",
                char_span=(c * 100, (c + 1) * 100),
                section_path=["body"],
            ))
            vec = rng.randn(dim).astype(np.float32)
            vec /= np.linalg.norm(vec)
            vecs.append(vec)
            ids.append(cid)
    matrix = np.stack(vecs, axis=0)
    store = VectorStore(ids=ids, matrix=matrix)
    return docs, chunks, store


def _dense_similarity_edges(vectors: VectorStore):
    """Reference dense implementation (the old code before blockwise)."""
    n = vectors.matrix.shape[0]
    k = min(KNN_K, n - 1)
    sims = vectors.matrix @ vectors.matrix.T
    np.fill_diagonal(sims, -1.0)

    knn_edges = set()
    strong_edges = set()
    for i in range(n):
        top = np.argpartition(-sims[i], k - 1)[:k]
        for j in top:
            knn_edges.add((vectors.ids[i], vectors.ids[int(j)]))
            if sims[i, j] >= STRONG_COS:
                strong_edges.add((vectors.ids[i], vectors.ids[int(j)]))
    return knn_edges, strong_edges


def test_blockwise_matches_dense_default():
    """Default block_size (1024) on 40 chunks -- single block."""
    docs, chunks, store = _make_corpus(n_docs=5, chunks_per_doc=8, dim=32)

    graph = build_corpus_graph(docs, chunks, store)
    blockwise_knn = set(map(tuple, graph.edges.get("similar_knn", [])))
    blockwise_strong = set(map(tuple, graph.edges.get("similar_strong", [])))

    dense_knn, dense_strong = _dense_similarity_edges(store)

    assert blockwise_knn == dense_knn, (
        f"knn mismatch: {len(blockwise_knn)} vs {len(dense_knn)}"
    )
    assert blockwise_strong == dense_strong, (
        f"strong mismatch: {len(blockwise_strong)} vs {len(dense_strong)}"
    )


def test_blockwise_matches_dense_small_block():
    """block_size=4 on 15 chunks -- exercises the multi-block path."""
    docs, chunks, store = _make_corpus(n_docs=3, chunks_per_doc=5, dim=16)
    assert store.matrix.shape[0] == 15  # sanity

    graph = build_corpus_graph(
        docs, chunks, store, similarity_block_size=4,
    )
    blockwise_knn = set(map(tuple, graph.edges.get("similar_knn", [])))
    blockwise_strong = set(map(tuple, graph.edges.get("similar_strong", [])))

    dense_knn, dense_strong = _dense_similarity_edges(store)

    assert blockwise_knn == dense_knn, (
        f"multi-block knn mismatch: {len(blockwise_knn)} vs {len(dense_knn)}"
    )
    assert blockwise_strong == dense_strong, (
        f"multi-block strong mismatch: "
        f"{len(blockwise_strong)} vs {len(dense_strong)}"
    )
