"""Guided ``write_now`` may dip into the reserved write headroom.

Issue 6 in ``docs/distill-test-readiness.md``: the standard pipeline
holds 95% of the planned write budget off-limits during extract, but
guided's mid-session ``write_now`` runs through ``_run_write_pass`` with
its own ``1.05 × budget`` guard, so the orchestrator can spend the
reserved write headroom before the final write pass.

This is **accepted treatment for guided**, not a bug. The tests pin
the contract:

- ``write_now`` propagates from the orchestrator as an ``ExtractDecision``
  with ``stop=True``;
- ``write_now`` is **never cached** by ``GuidedMode`` (so each fire is a
  fresh orchestrator decision and the operator can't be silently
  re-issued);
- the cost-meter hard ceiling (``1.05 × budget``) is independent of
  whether the reserve was respected and still bounds the run.
"""

from __future__ import annotations

import random

import pytest

from wikify.distill.explorer import (
    ExplorerState,
    GlobalOp,
    LevyExplorer,
    LocalOp,
)
from wikify.distill.strategy import GuidedMode, ModeContext, RuntimeOverrides
from wikify.meter import BudgetExceededError, CostMeter
from wikify.schema import OrchAction, OrchState
from wikify.types import Orchestrator


def _empty_state(seed: int = 0) -> ExplorerState:
    import networkx as nx

    from wikify.citestore.graph import KnowledgeGraph, NetworkXBackend

    backend = NetworkXBackend(G=nx.MultiDiGraph())
    return ExplorerState(
        rng=random.Random(seed),
        kg=KnowledgeGraph(backend=backend),
    )


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


class _ScriptedOrchestrator(Orchestrator):
    """Returns a fixed sequence of OrchActions, one per ``step`` call."""

    def __init__(self, actions: list[OrchAction]) -> None:
        self._actions = list(actions)
        self.call_count = 0

    def step(self, state: OrchState) -> OrchAction:
        self.call_count += 1
        if not self._actions:
            return OrchAction(name="done", args={}, tokens_in=0, tokens_out=0)
        return self._actions.pop(0)


def test_guided_propagates_write_now_with_stop_true():
    """``write_now`` from the orchestrator must surface as ``stop=True``."""
    orch = _ScriptedOrchestrator(
        [OrchAction(name="write_now", args={}, tokens_in=10, tokens_out=1)]
    )
    explorer = LevyExplorer(
        local_op=LocalOp.SIMILARITY_WALK,
        global_op=GlobalOp.COVERAGE_GAP,
        jump_rate=0.1,
    )
    mode = GuidedMode(orch, explorer, runtime=RuntimeOverrides())
    decision = mode.next_extract(_empty_state(), 4, _ctx())
    assert decision.action == "write_now"
    assert decision.stop is True
    assert decision.batch == ()


def test_guided_never_caches_write_now():
    """``write_now`` must be re-decided every batch — not silently replayed.

    The cache exists to amortise tier-L orchestrator cost over batches of
    cheap exploration actions. ``write_now`` is a control verb that
    re-times spending; caching it would cause the same write pass to
    fire multiple times in a row.
    """
    orch = _ScriptedOrchestrator(
        [
            OrchAction(name="write_now", args={}, tokens_in=10, tokens_out=1),
            OrchAction(name="done", args={}, tokens_in=10, tokens_out=1),
        ]
    )
    explorer = LevyExplorer(
        local_op=LocalOp.SIMILARITY_WALK,
        global_op=GlobalOp.COVERAGE_GAP,
        jump_rate=0.1,
    )
    mode = GuidedMode(orch, explorer, runtime=RuntimeOverrides(), persist_batches=8)
    state = _empty_state()
    first = mode.next_extract(state, 4, _ctx())
    second = mode.next_extract(state, 4, _ctx())
    assert first.action == "write_now"
    assert second.action == "done"
    assert orch.call_count == 2


def test_guided_meter_aborts_when_write_now_exhausts_budget(tmp_path):
    """The cost-meter ``BudgetExceededError`` is the backstop that bounds
    the accepted reserve consumption.

    The pipeline's extract-loop reserve protects the write phase under
    normal scripted operation. Guided's ``write_now`` deliberately runs
    a write pass mid-extract and may spend into that reserve. The
    backstop is the meter's hard ``1.05 × budget`` abort: even if the
    reserve is fully consumed, the next over-budget call raises and
    the run terminates cleanly.
    """
    budget = 1_000.0
    events_path = tmp_path / "calls.jsonl"
    meter = CostMeter(
        budget_haiku_eq=budget,
        run_id="ceiling-only",
        events_path=events_path,
    )
    from wikify.context import response_reserve, total_context
    from wikify.types import Role

    cap = total_context() - response_reserve()
    # Each call costs ~600 heq at tier M (200 input + 30 output + 200 overhead).
    # Two calls = 1200 heq ~ 1.2 × budget. The second call MUST abort.
    with pytest.raises(BudgetExceededError):
        for _ in range(5):
            meter.record(
                role=Role.WRITER,
                tier="M",
                input_tokens=200,
                output_tokens=30,
                context_cap=cap,
                wall_seconds=0.01,
                cache_hit=False,
                prompt_hash="fake",
            )
