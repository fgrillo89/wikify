"""Integration test: guided ``write_now`` actually runs a write pass mid-extract.

Pins the load-bearing piece of Issue 6 (memo: docs/distill-test-readiness.md):
when the orchestrator emits ``write_now`` mid-session, the pipeline runs
``run_write_pass`` BEFORE the extract loop terminates. The reserve is
allowed to be consumed (this is the accepted treatment difference); the
test asserts a write pass actually fires mid-extract.

GuidedMode caches active exploration actions for 8 batches before
re-querying the orchestrator, so the orchestrator returns
``jump_uniform`` first to seed candidates and ``write_now`` on the
second query.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from wikify.cache import ExtractCache
from wikify.context import response_reserve, total_context
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.strategy import build_strategy
from wikify.ingest.pipeline import ingest_corpus
from wikify.meter import CostMeter
from wikify.paths import BundlePaths, CorpusPaths
from wikify.schema import OrchAction, OrchState
from wikify.types import Orchestrator, Role

from .fakes import FakeExtractor, FakeWriter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture
def corpus(tmp_path) -> CorpusPaths:
    return ingest_corpus(FIXTURE, tmp_path / "corpus")


class _SeedThenWriteNowOrchestrator(Orchestrator):
    """First call: jump_uniform (seeds candidates). Second: write_now. Then done."""

    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter
        self._steps = 0

    def step(self, state: OrchState) -> OrchAction:
        self._steps += 1
        t0 = time.monotonic()
        if self._steps == 1:
            name, args = "jump_uniform", {"n_docs": 2}
        elif self._steps == 2:
            name, args = "write_now", {}
        else:
            name, args = "done", {}
        self._meter.record(
            role=Role.ORCHESTRATOR,
            tier="L",
            input_tokens=200,
            output_tokens=20,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash="seed-write-now",
        )
        return OrchAction(name=name, args=args, tokens_in=200, tokens_out=20)


def test_guided_write_now_runs_write_pass_mid_extract(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    # Budget large enough that the orchestrator gets re-queried after the
    # 8-batch cache window so write_now actually fires.
    budget = 100_000.0
    meter = CostMeter(
        budget_haiku_eq=budget,
        run_id="write-now-mid-extract",
        events_path=bundle.calls_path,
    )
    cfg = build_strategy("balanced", seed=0)

    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=budget,
        mode_name="guided",
        orchestrator=_SeedThenWriteNowOrchestrator(meter),
    )

    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    actions = snap.get("policy_actions", [])

    # The pipeline emits a "stage: write_now" marker each time write_now
    # actually triggered a mid-session write pass. Its presence proves
    # _run_write_pass ran during the extract loop, before the final
    # write phase. This is the accepted treatment for guided.
    write_now_events = [a for a in actions if a.get("stage") == "write_now"]
    assert write_now_events, (
        "Expected at least one stage=write_now event in policy_actions; "
        f"saw {[a.get('stage') + ':' + str(a.get('action', '')) for a in actions]}"
    )
    assert write_now_events[0]["n_pages"] >= 1, (
        "write_now fired but produced no pages — did seed extract emit candidates?"
    )

    # Hard meter contract still holds.
    assert meter.spent_haiku_eq <= 1.05 * budget
