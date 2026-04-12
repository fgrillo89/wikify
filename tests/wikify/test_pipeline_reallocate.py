"""Adaptive schedule reallocation after the extract loop completes.

When extract finishes normally (i.e. budget not exceeded) and the realised
novelty rate (unique concepts / chunks read) is below the AdaptiveBudget
threshold, ``Schedule.reallocate`` should bump the write share of the
remaining budget. The pipeline records ``split_initial`` and
``split_reallocated`` in the run snapshot so this is observable.
"""

import json
from pathlib import Path

import pytest

from .fakes import FakeExtractor, FakeWriter
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.explorer import GlobalOp, LevyExplorer, LocalOp
from wikify.distill.strategy import AdaptiveBudget
from wikify.distill.strategy import StrategyConfig
from wikify.cache import ExtractCache
from wikify.meter import CostMeter
from wikify.ingest.pipeline import ingest_corpus
from wikify.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("realloc_corpus")
    return ingest_corpus(FIXTURE, out)


def _strategy(seed: int = 0) -> StrategyConfig:
    return StrategyConfig(
        name="M",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        # very low novelty threshold so the small fixture trips it
        budget=AdaptiveBudget(
            exploit_fraction_initial=0.4,
            novelty_threshold=10.0,  # always triggers shift
        ),
        extract_tier="S",
        write_tier="M",
        seed=seed,
    )


def test_reallocate_records_initial_and_new_split(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=200_000.0,
        run_id="realloc-test",
        events_path=bundle.calls_path,
    )
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=_strategy(),
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=200_000.0,
    )
    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))

    assert "split_initial" in snap
    assert "split_reallocated" in snap
    assert "novelty_rate_at_reallocation" in snap

    init = snap["split_initial"]
    new = snap["split_reallocated"]
    # exploit_fraction_initial=0.4 with novelty<threshold -> 0.7 floor.
    # The reallocated write share should be strictly higher than the
    # initial write share once the threshold trips.
    assert new["write_haiku_eq"] > init["write_haiku_eq"]
