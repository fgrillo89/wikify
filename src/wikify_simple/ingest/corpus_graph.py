"""Build the typed CorpusGraph from chunks + the vector store.

Materialises the six edge kinds declared in models.CorpusGraph:
``contains``, ``similar_knn``, ``similar_strong`` (cos >= 0.75),
``co_section``, ``cites`` (only if doc metadata carries citation pairs),
and ``doc_similar`` (mean-pooled per-doc cosine >= 0.75).
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..models import Chunk, CorpusGraph, Document
from ..store.vectors import VectorStore

_KNN_K = 10
_STRONG_COS = 0.75
_DOC_SIM_COS = 0.75


def build_corpus_graph(
    docs: list[Document],
    chunks: list[Chunk],
    vectors: VectorStore,
) -> CorpusGraph:
    nodes: dict[str, dict] = {}
    for d in docs:
        nodes[d.id] = {"kind": "doc", "title": d.title}
    for c in chunks:
        nodes[c.id] = {"kind": "chunk", "doc_id": c.doc_id}

    edges: dict[str, list[tuple[str, str]]] = defaultdict(list)

    # contains
    for c in chunks:
        edges["contains"].append((c.doc_id, c.id))

    # co_section: same doc + same section_path
    by_section: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for c in chunks:
        by_section[(c.doc_id, tuple(c.section_path))].append(c.id)
    for ids in by_section.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                edges["co_section"].append((ids[i], ids[j]))

    # similarity edges
    if vectors.matrix.shape[0] >= 2:
        sims = vectors.matrix @ vectors.matrix.T
        np.fill_diagonal(sims, -1.0)
        n = sims.shape[0]
        k = min(_KNN_K, n - 1)
        for i in range(n):
            top = np.argpartition(-sims[i], k - 1)[:k]
            for j in top:
                edges["similar_knn"].append((vectors.ids[i], vectors.ids[int(j)]))
                if sims[i, j] >= _STRONG_COS:
                    edges["similar_strong"].append((vectors.ids[i], vectors.ids[int(j)]))

    # doc_similar via mean-pooled per-doc embeddings
    by_doc_idx: dict[str, list[int]] = defaultdict(list)
    for i, cid in enumerate(vectors.ids):
        node = nodes.get(cid)
        if node and node.get("kind") == "chunk":
            by_doc_idx[node["doc_id"]].append(i)
    doc_ids = sorted(by_doc_idx)
    if len(doc_ids) >= 2:
        means = np.stack([vectors.matrix[by_doc_idx[d]].mean(0) for d in doc_ids])
        norms = np.linalg.norm(means, axis=1, keepdims=True)
        means = means / np.where(norms > 0, norms, 1.0)
        dsim = means @ means.T
        np.fill_diagonal(dsim, -1.0)
        for i in range(len(doc_ids)):
            for j in range(i + 1, len(doc_ids)):
                if dsim[i, j] >= _DOC_SIM_COS:
                    edges["doc_similar"].append((doc_ids[i], doc_ids[j]))

    # cites (optional, from doc metadata)
    for d in docs:
        cited = d.metadata.get("cites") or []
        for target in cited:
            if target in nodes:
                edges["cites"].append((d.id, target))

    return CorpusGraph(nodes=nodes, edges=dict(edges))
