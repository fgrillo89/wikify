"""Coverage-gap and policy-layer regression tests."""

import json
import random
from pathlib import Path

import numpy as np
import pytest

from .fakes import FakeExtractor, FakeOrchestrator, FakeWriter
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.explorer import (
    GlobalOp,
    LevyExplorer,
    LocalOp,
    ExplorerState,
    apply_coverage_feedback,
    init_coverage_state,
    restore_coverage_state,
)
from wikify.distill.strategy import StaticBudget
from wikify.distill.strategy import StrategyConfig
from wikify.cache import ExtractCache
from wikify.meter import CostMeter
from wikify.ingest.pipeline import ingest_corpus
from wikify.models import CorpusGraph
from wikify.paths import BundlePaths, CorpusPaths
from wikify.store.vectors import VectorStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


def _synthetic_state() -> ExplorerState:
    ids = ["c1", "c2", "c3", "c4"]
    state = ExplorerState(
        rng=random.Random(0),
        graph=CorpusGraph(
            nodes={},
            edges={
                "similar_strong": [("c1", "c2"), ("c3", "c4")],
                "co_section": [],
            },
        ),
        vectors=VectorStore(ids=ids, matrix=np.eye(4, dtype=np.float32)),
        chunks_by_doc={"d1": ["c1", "c2"], "d2": ["c3", "c4"]},
        abstract_chunk_by_doc={"d1": "c1", "d2": "c3"},
        pagerank_doc={"d1": 0.5, "d2": 0.5},
        neighbors_by_chunk={"c1": ("c2",), "c2": ("c1",), "c3": ("c4",), "c4": ("c3",)},
        chunk_degree={"c1": 1, "c2": 1, "c3": 1, "c4": 1},
        chunk_to_doc={"c1": "d1", "c2": "d1", "c3": "d2", "c4": "d2"},
    )
    init_coverage_state(state, ids)
    return state


def test_coverage_gap_picks_highest_residual_unseen():
    state = _synthetic_state()
    restore_coverage_state(
        state,
        residuals={"c1": 0.10, "c2": 0.95, "c3": 0.80, "c4": 0.30},
        seen_chunks={"c3"},
        doc_seen_counts={"d2": 1},
    )
    sampler = LevyExplorer(local_op=LocalOp.NONE, global_op=GlobalOp.COVERAGE_GAP, jump_rate=1.0)
    batch = sampler.next_batch(state, 1)
    assert batch == ["c2"]


def test_coverage_feedback_discounts_seen_chunk_and_neighbors():
    state = _synthetic_state()
    apply_coverage_feedback(state, "c1", as_evidence=True)
    assert state.coverage_residuals["c1"] == 0.0
    assert state.coverage_residuals["c2"] <= 0.2


@pytest.fixture
def corpus(tmp_path) -> CorpusPaths:
    return ingest_corpus(FIXTURE, tmp_path / "corpus")


def test_llm_policy_records_actions_in_snapshot(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=30_000.0,
        run_id="llm-policy-test",
        events_path=bundle.calls_path,
    )
    cfg = StrategyConfig(
        name="M",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        budget=StaticBudget(exploit_fraction=0.4),
        extract_tier="S",
        write_tier="S",
        seed=0,
    )
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=30_000.0,
        mode_name="guided",
        orchestrator=FakeOrchestrator(meter, max_steps=2),
    )
    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
    assert snap["mode"] == "guided"
    assert any(ev.get("mode") == "guided" for ev in snap.get("policy_actions", []))
