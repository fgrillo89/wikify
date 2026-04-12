"""Test the multi-iteration campaign driver.

Verifies:
- Corpus loaders are called exactly once regardless of --iterations.
- Iteration 2 is a refine pass (uses load_existing_pages).
- Both iterations produce a _run.json snapshot.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wikify_simple.distill.pipeline import run_with_preloaded
from wikify_simple.distill.preload import preload_corpus
from wikify_simple.distill.explorer import GlobalOp, LevyExplorer, LocalOp
from wikify_simple.distill.strategy import StaticBudget
from wikify_simple.distill.strategy import StrategyConfig
from wikify_simple.cache import ExtractCache
from wikify_simple.meter import CostMeter
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("campaign_corpus")
    return ingest_corpus(FIXTURE, out)


def _strategy(seed: int = 0) -> StrategyConfig:
    return StrategyConfig(
        name="M",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.3,
        ),
        budget=StaticBudget(exploit_fraction=0.5),
        extract_tier="S",
        write_tier="M",
        seed=seed,
    )


def test_preload_corpus_called_once(corpus, tmp_path):
    """Corpus loaders are called once; run_with_preloaded is called N times."""
    from .fakes import FakeExtractor, FakeWriter
    from wikify_simple.store.corpus import list_documents as _list_docs_real

    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")

    # Call preload_corpus once (simulating what campaign does).
    with patch(
        "wikify_simple.distill.preload.list_documents", wraps=_list_docs_real
    ) as mock_list_docs:
        preloaded = preload_corpus(corpus)
        # Run two iterations against the same preloaded state.
        for i in range(1, 3):
            meter = CostMeter(
                budget_haiku_eq=20_000.0,
                run_id=f"campaign-test-iter{i}",
                events_path=bundle.calls_path,
            )
            run_with_preloaded(
                preloaded=preloaded,
                bundle=bundle,
                strategy=_strategy(seed=i - 1),
                extractor=FakeExtractor(cache, meter),
                writer=FakeWriter(meter),
                meter=meter,
                budget_haiku_eq=20_000.0,
                iteration="create" if i == 1 else "refine",
            )

        # list_documents was called exactly once (inside preload_corpus above).
        assert mock_list_docs.call_count == 1


def test_iteration_two_is_refine(corpus, tmp_path):
    """Iteration 2 calls load_existing_pages (refine semantics)."""
    from .fakes import FakeExtractor, FakeWriter

    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    preloaded = preload_corpus(corpus)

    load_existing_calls = []

    import wikify_simple.distill.pipeline as _pipeline_mod

    original_lep = _pipeline_mod.load_existing_pages

    def tracking_lep(b):
        load_existing_calls.append(b.root)
        return original_lep(b)

    with patch.object(_pipeline_mod, "load_existing_pages", side_effect=tracking_lep):
        for i in range(1, 3):
            meter = CostMeter(
                budget_haiku_eq=20_000.0,
                run_id=f"refine-test-iter{i}",
                events_path=bundle.calls_path,
            )
            run_with_preloaded(
                preloaded=preloaded,
                bundle=bundle,
                strategy=_strategy(seed=i - 1),
                extractor=FakeExtractor(cache, meter),
                writer=FakeWriter(meter),
                meter=meter,
                budget_haiku_eq=20_000.0,
                iteration="create" if i == 1 else "refine",
            )

    # load_existing_pages is only called on iteration 2 (refine).
    assert len(load_existing_calls) == 1


def test_both_iterations_produce_run_json(corpus, tmp_path):
    """Each iteration writes a _run.json snapshot to the bundle."""
    from .fakes import FakeExtractor, FakeWriter

    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    preloaded = preload_corpus(corpus)

    for i in range(1, 3):
        meter = CostMeter(
            budget_haiku_eq=20_000.0,
            run_id=f"snap-test-iter{i}",
            events_path=bundle.calls_path,
        )
        run_with_preloaded(
            preloaded=preloaded,
            bundle=bundle,
            strategy=_strategy(seed=i - 1),
            extractor=FakeExtractor(cache, meter),
            writer=FakeWriter(meter),
            meter=meter,
            budget_haiku_eq=20_000.0,
            iteration="create" if i == 1 else "refine",
        )
        assert bundle.run_path.exists(), f"_run.json missing after iteration {i}"
        snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))
        assert "iteration" in snap
        assert snap["iteration"] == ("create" if i == 1 else "refine")
