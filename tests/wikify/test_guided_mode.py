"""Tests for the LLM policy's control actions: set_tier, set_allocation, pick_chunks."""

import random

import numpy as np
import pytest

from wikify.distill.explorer import (
    ExplorerState,
    GlobalOp,
    LevyExplorer,
    LocalOp,
    build_snapshot,
    init_coverage_state,
    semantic_query_chunks,
)
from wikify.distill.strategy import (
    GuidedMode,
    ModeContext,
    RuntimeOverrides,
)
from wikify.models import CorpusGraph
from wikify.schema import OrchAction
from wikify.store.vectors import VectorStore


class _ScriptedOrchestrator:
    """Returns a preset sequence of actions on each step()."""

    def __init__(self, actions: list[OrchAction]) -> None:
        self._actions = iter(actions)

    def step(self, state) -> OrchAction:  # noqa: ARG002
        return next(self._actions)


def _explorer() -> LevyExplorer:
    return LevyExplorer(local_op=LocalOp.NONE, global_op=GlobalOp.UNIFORM, jump_rate=1.0)


def _ctx() -> ModeContext:
    return ModeContext(
        run_id="t",
        n_pages=0,
        n_candidates=0,
        n_concepts=0,
        n_people=0,
        docs_covered=0,
        docs_total=0,
    )


def test_set_tier_mutates_runtime():
    rt = RuntimeOverrides()
    assert rt.write_tier == "M"
    orch = _ScriptedOrchestrator([OrchAction(name="set_tier", args={"role": "write", "tier": "L"})])
    policy = GuidedMode(orch, _explorer(), runtime=rt)
    decision = policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert decision.action == "set_tier"
    assert decision.batch == ()
    assert rt.write_tier == "L"


def test_set_tier_locked_for_orchestrator():
    rt = RuntimeOverrides()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_tier", args={"role": "orchestrate", "tier": "S"})]
    )
    policy = GuidedMode(orch, _explorer(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.orchestrate_tier == "L"


def test_set_tier_rejects_invalid_tier():
    rt = RuntimeOverrides()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_tier", args={"role": "extract", "tier": "Q"})]
    )
    policy = GuidedMode(orch, _explorer(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.extract_tier == "S"


def test_set_allocation_mutates_runtime_and_bumps_epoch():
    rt = RuntimeOverrides()
    assert rt.exploit_fraction is None
    assert rt.allocation_epoch == 0
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_allocation", args={"exploit_fraction": 0.7})]
    )
    policy = GuidedMode(orch, _explorer(), runtime=rt)
    decision = policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert decision.action == "set_allocation"
    assert rt.exploit_fraction == 0.7
    assert rt.allocation_epoch == 1


def test_set_allocation_rejects_out_of_range():
    rt = RuntimeOverrides()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="set_allocation", args={"exploit_fraction": 1.5})]
    )
    policy = GuidedMode(orch, _explorer(), runtime=rt)
    policy.next_extract(state=object(), k=4, ctx=_ctx())
    assert rt.exploit_fraction is None
    assert rt.allocation_epoch == 0


# --- pick_chunks tests ---------------------------------------------------


def _explorer_state_with_seen() -> ExplorerState:
    """Tiny ExplorerState with 4 chunks; c1/c2 already seen."""
    ids = ["c1", "c2", "c3", "c4"]
    state = ExplorerState(
        rng=random.Random(0),
        graph=CorpusGraph(nodes={}, edges={}),
        vectors=VectorStore(ids=ids, matrix=np.eye(4, dtype=np.float32)),
        chunks_by_doc={"d1": ["c1", "c2"], "d2": ["c3", "c4"]},
        abstract_chunk_by_doc={},
        pagerank_doc={},
        chunk_to_doc={"c1": "d1", "c2": "d1", "c3": "d2", "c4": "d2"},
    )
    init_coverage_state(state, ids)
    state.seen_chunks.add("c1")
    state.seen_chunks.add("c2")
    state.doc_seen_counts["d1"] = 2
    return state


def test_pick_chunks_returns_novel_ids():
    state = _explorer_state_with_seen()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="pick_chunks", args={"chunk_ids": ["c3", "c4"], "reason": "test"})]
    )
    policy = GuidedMode(orch, _explorer())
    decision = policy.next_extract(state=state, k=4, ctx=_ctx())
    assert decision.action == "pick_chunks"
    assert set(decision.batch) == {"c3", "c4"}
    assert decision.stop is False


def test_pick_chunks_filters_already_seen():
    state = _explorer_state_with_seen()
    # c1 and c2 are already seen; only c3 is novel here
    orch = _ScriptedOrchestrator(
        [OrchAction(name="pick_chunks", args={"chunk_ids": ["c1", "c2", "c3"], "reason": "dedup"})]
    )
    policy = GuidedMode(orch, _explorer())
    decision = policy.next_extract(state=state, k=4, ctx=_ctx())
    assert decision.batch == ("c3",)
    assert decision.meta["n_requested"] == 3
    assert decision.meta["n_novel"] == 1


def test_pick_chunks_all_seen_returns_empty_batch():
    state = _explorer_state_with_seen()
    # All requested chunks are already seen
    orch = _ScriptedOrchestrator(
        [OrchAction(name="pick_chunks", args={"chunk_ids": ["c1", "c2"], "reason": "empty"})]
    )
    policy = GuidedMode(orch, _explorer())
    decision = policy.next_extract(state=state, k=4, ctx=_ctx())
    assert decision.batch == ()
    assert decision.meta["n_novel"] == 0


def test_pick_chunks_reason_logged_in_events():
    state = _explorer_state_with_seen()
    orch = _ScriptedOrchestrator(
        [OrchAction(name="pick_chunks", args={"chunk_ids": ["c3"], "reason": "semantic hit"})]
    )
    policy = GuidedMode(orch, _explorer())
    policy.next_extract(state=state, k=4, ctx=_ctx())
    events = policy.drain_events()
    assert len(events) == 1
    assert events[0]["reason"] == "semantic hit"


def test_pick_chunks_not_cached():
    """pick_chunks must NOT be cached; the next call must re-query the orchestrator."""
    state = _explorer_state_with_seen()
    orch = _ScriptedOrchestrator(
        [
            OrchAction(name="pick_chunks", args={"chunk_ids": ["c3"], "reason": "first"}),
            OrchAction(name="pick_chunks", args={"chunk_ids": ["c4"], "reason": "second"}),
        ]
    )
    policy = GuidedMode(orch, _explorer(), persist_batches=8)
    d1 = policy.next_extract(state=state, k=4, ctx=_ctx())
    d2 = policy.next_extract(state=state, k=4, ctx=_ctx())
    # Both calls should go to the orchestrator, returning different batches.
    assert d1.batch == ("c3",)
    assert d2.batch == ("c4",)


# --- sampler_snapshot tests -----------------------------------------------


def test_sampler_snapshot_content():
    state = _explorer_state_with_seen()
    snap = build_snapshot(state)
    assert "top_gap_chunks" in snap
    assert "doc_coverage" in snap
    assert "content_stats" in snap


def test_sampler_snapshot_top_gap_chunks_excludes_seen():
    state = _explorer_state_with_seen()
    snap = build_snapshot(state)
    seen_in_gap = [e["chunk_id"] for e in snap["top_gap_chunks"] if e["chunk_id"] in {"c1", "c2"}]
    assert seen_in_gap == []


def test_sampler_snapshot_doc_coverage_matches_seen_counts():
    state = _explorer_state_with_seen()
    snap = build_snapshot(state)
    # d1 has 2 seen chunks
    assert snap["doc_coverage"].get("d1") == 2
    # d2 has 0 seen chunks, should not appear
    assert "d2" not in snap["doc_coverage"]


def test_sampler_snapshot_content_stats():
    state = _explorer_state_with_seen()
    snap = build_snapshot(state)
    stats = snap["content_stats"]
    assert stats["n_seen"] == 2
    assert stats["n_chunks"] == 4


def test_sampler_snapshot_capped_at_20():
    """top_gap_chunks must contain at most 20 entries."""
    ids = [f"c{i}" for i in range(50)]
    state = ExplorerState(
        rng=random.Random(0),
        graph=CorpusGraph(nodes={}, edges={}),
        vectors=VectorStore(ids=ids, matrix=np.eye(50, dtype=np.float32)),
        chunks_by_doc={"d1": ids},
        abstract_chunk_by_doc={},
        pagerank_doc={},
        chunk_to_doc={cid: "d1" for cid in ids},
    )
    init_coverage_state(state, ids)
    snap = build_snapshot(state)
    assert len(snap["top_gap_chunks"]) <= 20


# --- semantic_query_chunks tests -----------------------------------------


def _four_chunk_state() -> ExplorerState:
    """4 chunks with orthogonal unit vectors for deterministic cosine tests."""
    ids = ["c1", "c2", "c3", "c4"]
    matrix = np.eye(4, dtype=np.float32)
    state = ExplorerState(
        rng=random.Random(0),
        graph=CorpusGraph(nodes={}, edges={}),
        vectors=VectorStore(ids=ids, matrix=matrix),
        chunks_by_doc={"d1": ["c1", "c2"], "d2": ["c3", "c4"]},
        abstract_chunk_by_doc={},
        pagerank_doc={},
        chunk_to_doc={"c1": "d1", "c2": "d1", "c3": "d2", "c4": "d2"},
    )
    init_coverage_state(state, ids)
    return state


def test_semantic_query_all_returns_topk():
    state = _four_chunk_state()
    # query vector aligned with c1 (first basis vector)
    query = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    results = semantic_query_chunks(state, query, k=2, scope="all")
    assert len(results) == 2
    assert results[0]["chunk_id"] == "c1"
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-5)


def test_semantic_query_unseen_excludes_seen():
    state = _four_chunk_state()
    state.seen_chunks.add("c1")
    query = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    results = semantic_query_chunks(state, query, k=4, scope="unseen")
    chunk_ids = [r["chunk_id"] for r in results]
    assert "c1" not in chunk_ids


def test_semantic_query_page_scope_filters_by_doc():
    state = _four_chunk_state()
    query = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
    # scope "page:d2" -> only chunks belonging to d2 (c3, c4)
    results = semantic_query_chunks(state, query, k=4, scope="page:d2")
    chunk_ids = {r["chunk_id"] for r in results}
    assert chunk_ids == {"c3", "c4"}


def test_semantic_query_empty_store_returns_empty():
    state = ExplorerState(
        rng=random.Random(0),
        graph=CorpusGraph(nodes={}, edges={}),
        vectors=VectorStore(ids=[], matrix=np.zeros((0, 4), dtype=np.float32)),
        chunks_by_doc={},
        abstract_chunk_by_doc={},
        pagerank_doc={},
    )
    query = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    results = semantic_query_chunks(state, query, k=5, scope="all")
    assert results == []
