"""Greedy submodular seed selection — deterministic primitive for any workflow skill.

The selector ranks candidate documents by the objective::

    score(d | S) = pagerank_weight * pr_norm(d)
                 + (1 - pagerank_weight) * coverage_gain(d | S)

where ``coverage_gain`` is computed over mean-pooled non-reference,
non-caption document embeddings with clipped cosine similarities.

This module owns the ranking only. ``pagerank_weight`` and ``max_seeds``
are caller-supplied — the CLI surface (``corpus find --seed --max
<n> --pagerank-weight <w>``) carries the defaults so the value is
explicitly chosen at the agent boundary, not buried in a Python config
object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ..ingest.config import SKIP_SECTION_TYPES
from ..models import Chunk

if TYPE_CHECKING:
    from wikify.corpus.graph import KnowledgeGraph
    from wikify.corpus.vectors import VectorStore


def doc_embeddings(
    chunks: list[Chunk],
    vectors: VectorStore | None,
) -> tuple[np.ndarray, list[str]]:
    """Mean-pool chunk embeddings per doc over usable chunks.

    Excludes references / acknowledgments / appendix sections (per
    ``SKIP_SECTION_TYPES``) and caption chunks (``__image__`` section
    head). Returns row-major ``(D x dim)`` plus the doc-id row order.
    """
    if vectors is None or not vectors.ids:
        return np.zeros((0, 0), dtype=np.float32), []

    id_to_row = {cid: i for i, cid in enumerate(vectors.ids)}
    chunks_by_doc: dict[str, list[int]] = {}
    for ck in chunks:
        if ck.section_type in SKIP_SECTION_TYPES:
            continue
        sp = list(ck.section_path or [])
        if sp and sp[0] == "__image__":
            continue
        row = id_to_row.get(ck.id)
        if row is None:
            continue
        chunks_by_doc.setdefault(ck.doc_id, []).append(row)

    doc_order = sorted(chunks_by_doc)
    if not doc_order:
        return np.zeros((0, vectors.matrix.shape[1]), dtype=np.float32), []

    rows = []
    for did in doc_order:
        idx = chunks_by_doc[did]
        v = vectors.matrix[idx].mean(axis=0)
        n = float(np.linalg.norm(v))
        if n > 0:
            v = v / n
        rows.append(v)
    return np.vstack(rows).astype(np.float32), doc_order


def pagerank_normalised(kg: KnowledgeGraph, doc_order: list[str]) -> np.ndarray:
    """Linearly rescale corpus-source PageRank to ``[0, 1]`` over doc_order.

    A flat PageRank (e.g., a corpus where everyone cites everyone) maps
    to a constant 0.5 so the seed scorer falls back cleanly on the
    coverage term.
    """
    if not doc_order:
        return np.zeros((0,), dtype=np.float32)
    raw = np.zeros(len(doc_order), dtype=np.float32)
    src_index = {did: i for i, did in enumerate(doc_order)}
    for source in kg.sources(kind="corpus").collect():
        i = src_index.get(source["id"])
        if i is None:
            continue
        pr = source.get("pagerank")
        if pr is None:
            continue
        raw[i] = float(pr)
    lo = float(raw.min()) if raw.size else 0.0
    hi = float(raw.max()) if raw.size else 0.0
    if hi - lo < 1e-12:
        return np.full_like(raw, 0.5)
    return (raw - lo) / (hi - lo)


def greedy_seed_select(
    *,
    doc_order: list[str],
    doc_embeddings: np.ndarray,
    pr_norm: np.ndarray,
    max_seeds: int,
    pagerank_weight: float,
) -> list[str]:
    """Greedy submodular: ``pagerank_weight * pr + (1-w) * cov_gain``.

    Returns up to ``max_seeds`` doc ids ordered by selection (highest
    score first). The selector does not know about budgets in token
    units — callers translate budgets into ``max_seeds`` themselves and
    rely on their own per-call gate to bound real spend.
    """
    n = len(doc_order)
    if n == 0 or max_seeds <= 0:
        return []

    sims = doc_embeddings @ doc_embeddings.T
    np.maximum(sims, 0.0, out=sims)

    max_sim_to_s = np.zeros(n, dtype=np.float32)
    selected: list[int] = []
    cap = min(max_seeds, n)
    pr_w = float(pagerank_weight)
    cov_w = 1.0 - pr_w

    while len(selected) < cap:
        gain_terms = sims - max_sim_to_s[:, None]
        np.maximum(gain_terms, 0.0, out=gain_terms)
        coverage_gain = gain_terms.sum(axis=0)
        for s in selected:
            coverage_gain[s] = -1.0
        if coverage_gain.max() <= 0:
            cov_norm = np.zeros_like(coverage_gain)
        else:
            cov_norm = np.clip(coverage_gain / coverage_gain.max(), 0.0, 1.0)
        score = pr_w * pr_norm + cov_w * cov_norm
        for s in selected:
            score[s] = -np.inf
        best = int(np.argmax(score))
        if not np.isfinite(score[best]):
            break
        selected.append(best)
        max_sim_to_s = np.maximum(max_sim_to_s, sims[:, best])

    return [doc_order[i] for i in selected]


def select_seeded_bootstrap(
    *,
    chunks: list[Chunk],
    vectors: VectorStore | None,
    kg: KnowledgeGraph,
    max_seeds: int,
    pagerank_weight: float,
) -> list[str]:
    """End-to-end: pick seed docs and return their canonical abstract chunks.

    The abstract chunk for each seed doc comes from
    ``kg.source(d).abstract_chunk()`` — the canonical reader for the
    ingest-time-tagged abstract. Docs with no body content (no abstract
    tag) are silently skipped.
    """
    embeds, doc_order = doc_embeddings(chunks, vectors)
    pr_norm = pagerank_normalised(kg, doc_order)
    seed_doc_ids = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=max_seeds,
        pagerank_weight=pagerank_weight,
    )
    return [
        chunk["id"]
        for did in seed_doc_ids
        if (chunk := kg.source(did).abstract_chunk()) is not None
    ]
