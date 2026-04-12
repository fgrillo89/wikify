"""Build the typed CorpusGraph from chunks + the vector store.

Materialises the seven edge kinds in the saved graph.json:
``contains`` (doc → chunk), ``similar_knn`` (top-k cosine), ``similar_strong``
(cos >= STRONG_COS), ``co_section`` (same doc + same heading path),
``cites`` (directed doc → doc, from the resolved citation graph),
``doc_similar`` (mean-pooled per-doc cosine >= DOC_SIM_COS), and
``cites_same`` (undirected bibliographic coupling — pairs of docs that
share at least ``min_strength`` references).

Order matters: this builder must run AFTER ``_populate_doc_edges`` in
``refresh.py`` so the doc-side ``cites`` / ``cites_same`` lists are
populated. Pre-fix it ran earlier and the citation/coupling edges were
silently empty.
"""

from collections import defaultdict

import numpy as np

from ..models import Chunk, CorpusGraph, Document
from ..store.vectors import VectorStore
from .config import DOC_SIM_COS, KNN_K, STRONG_COS


def build_corpus_graph(
    docs: list[Document],
    chunks: list[Chunk],
    vectors: VectorStore,
    *,
    similarity_block_size: int = 1024,
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

    # similarity edges -- blockwise to bound peak memory at
    # block_size * n_chunks instead of n_chunks^2.
    if vectors.matrix.shape[0] >= 2:
        n = vectors.matrix.shape[0]
        k = min(KNN_K, n - 1)
        for start in range(0, n, similarity_block_size):
            end = min(start + similarity_block_size, n)
            block = vectors.matrix[start:end]
            sims = block @ vectors.matrix.T  # shape: (block_size, n)
            # Zero out self-similarities within the block
            for local_i in range(end - start):
                sims[local_i, start + local_i] = -1.0
            for local_i in range(end - start):
                global_i = start + local_i
                row = sims[local_i]
                top = np.argpartition(-row, k - 1)[:k]
                for j in top:
                    edges["similar_knn"].append(
                        (vectors.ids[global_i], vectors.ids[int(j)])
                    )
                    if row[j] >= STRONG_COS:
                        edges["similar_strong"].append(
                            (vectors.ids[global_i], vectors.ids[int(j)])
                        )

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
                if dsim[i, j] >= DOC_SIM_COS:
                    edges["doc_similar"].append((doc_ids[i], doc_ids[j]))

    # cites: directed doc→doc edges from the resolved citation graph.
    # We read ``Document.cites`` directly (the post-embedding fuzzy
    # matcher in refresh.py populates it). The previous version of this
    # block read ``d.metadata["cites"]`` which was never set, so the
    # citation edges in the corpus graph were silently empty for the
    # entire history of this module.
    for d in docs:
        cited = d.cites or d.metadata.get("cites") or []
        for target in cited:
            if target in nodes:
                edges["cites"].append((d.id, target))

    # cites_same: undirected bibliographic-coupling edges. Two docs are
    # coupled when they share references; ``compute_coupling`` produces
    # the per-doc top-k list and refresh.py stores it on
    # ``Document.cites_same``. We surface it as a graph edge kind so
    # corpus_profile, pagerank, and community detection can use it as
    # a signal alongside cites and doc_similar.
    coupled_seen: set[tuple[str, str]] = set()
    for d in docs:
        for target in d.cites_same or []:
            if target not in nodes or target == d.id:
                continue
            key = tuple(sorted((d.id, target)))
            if key in coupled_seen:
                continue
            coupled_seen.add(key)
            edges["cites_same"].append(key)

    return CorpusGraph(nodes=nodes, edges=dict(edges))
