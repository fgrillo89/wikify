"""Synthetic scale regressions for sampler and crosslink hot paths."""

import random
import time

import numpy as np

from wikify_simple.distill.sampler import (
    GlobalOp,
    LevyMixSampler,
    LocalOp,
    SamplerState,
    apply_coverage_feedback,
    init_coverage_state,
)
from wikify_simple.distill.write.crosslink import crosslink
from wikify_simple.models import CorpusGraph, Evidence, WikiPage
from wikify_simple.store.vectors import VectorStore


def _sampler_state(n_docs: int, chunks_per_doc: int) -> SamplerState:
    rng = random.Random(0)
    chunk_ids: list[str] = []
    chunks_by_doc: dict[str, list[str]] = {}
    chunk_to_doc: dict[str, str] = {}
    for d in range(n_docs):
        did = f"d{d}"
        cids = [f"{did}_c{i}" for i in range(chunks_per_doc)]
        chunks_by_doc[did] = cids
        chunk_ids.extend(cids)
        for cid in cids:
            chunk_to_doc[cid] = did
    edges: list[tuple[str, str]] = []
    for did, cids in chunks_by_doc.items():
        for i in range(len(cids) - 1):
            edges.append((cids[i], cids[i + 1]))
    # sparse cross-doc links
    for i in range(0, len(chunk_ids) - 10, 10):
        edges.append((chunk_ids[i], chunk_ids[i + 10]))
    neighbors: dict[str, set[str]] = {cid: set() for cid in chunk_ids}
    for a, b in edges:
        neighbors[a].add(b)
        neighbors[b].add(a)
    rnd = np.random.default_rng(0)
    matrix = rnd.standard_normal((len(chunk_ids), 16), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms > 0, norms, 1.0)
    state = SamplerState(
        rng=rng,
        graph=CorpusGraph(nodes={}, edges={"similar_strong": edges, "co_section": []}),
        vectors=VectorStore(ids=chunk_ids, matrix=matrix),
        chunks_by_doc=chunks_by_doc,
        abstract_chunk_by_doc={d: cids[0] for d, cids in chunks_by_doc.items()},
        pagerank_doc={d: 1.0 / n_docs for d in chunks_by_doc},
        neighbors_by_chunk={cid: tuple(sorted(ns)) for cid, ns in neighbors.items()},
        chunk_degree={cid: len(ns) for cid, ns in neighbors.items()},
        chunk_to_doc=chunk_to_doc,
        pages_concept_evidence_chunks=[chunk_ids[0]],
    )
    init_coverage_state(state, chunk_ids)
    return state


def _run_sampler_workload(n_docs: int) -> float:
    state = _sampler_state(n_docs=n_docs, chunks_per_doc=5)
    sampler = LevyMixSampler(
        local_op=LocalOp.SIMILARITY_WALK,
        global_op=GlobalOp.COVERAGE_GAP,
        jump_rate=0.15,
    )
    t0 = time.perf_counter()
    for _ in range(160):
        batch = sampler.next_batch(state, 4)
        for cid in batch:
            apply_coverage_feedback(state, cid, as_evidence=False)
            state.pages_concept_evidence_chunks.append(cid)
    return time.perf_counter() - t0


def test_sampler_scaling_regression():
    small = _run_sampler_workload(120)
    large = _run_sampler_workload(360)  # 3x docs
    # Empirical guardrail: large run should not explode quadratically.
    assert large / max(small, 1e-6) < 12.0


def _pages(n: int) -> list[WikiPage]:
    out: list[WikiPage] = []
    for i in range(n):
        aliases = [f"alpha {i}", f"beta {i}", f"gamma {i}"]
        body = (
            f"This page discusses alpha {i} and beta {i}. "
            f"It also references alpha {max(0, i - 1)} from nearby work."
        )
        out.append(
            WikiPage(
                id=f"p{i}",
                kind="article",
                title=f"Concept {i}",
                aliases=aliases,
                body_markdown=body,
                evidence=[Evidence(marker="e1", chunk_id=f"c{i}", doc_id=f"d{i % 40}", quote="q")],
            )
        )
    return out


def _run_crosslink_workload(n: int) -> float:
    pages = _pages(n)
    t0 = time.perf_counter()
    crosslink(pages)
    return time.perf_counter() - t0


def test_crosslink_scaling_regression():
    small = _run_crosslink_workload(120)
    large = _run_crosslink_workload(360)  # 3x pages
    assert large / max(small, 1e-6) < 12.0
