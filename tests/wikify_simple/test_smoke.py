"""End-to-end smoke test: ingest -> distill (E, M, X) -> eval, all under fake binding."""

from pathlib import Path

import pytest

from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategy import build_strategy
from wikify_simple.eval.bundle import load_bundle
from wikify_simple.eval.metrics import (
    concept_recall,
    coverage_residual,
    grounding,
    heaps_exponent,
    hit_rate,
    person_recall,
    spectral_gap_modularity,
)
from wikify_simple.cache import ExtractCache
from wikify_simple.meter import CostMeter
from wikify_simple.embedding import embed_texts
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("corpus")
    return ingest_corpus(FIXTURE, out)


@pytest.mark.parametrize("strategy", ["E", "M", "X"])
def test_distill_produces_bundle(strategy, corpus, tmp_path):
    from .fakes import FakeExtractor, FakeWriter

    bundle = BundlePaths(root=tmp_path / f"{strategy}_1x_seed0")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id=f"{strategy}_1x_seed0",
        events_path=bundle.calls_path,
    )
    extractor = FakeExtractor(cache, meter)
    writer = FakeWriter(meter)

    cfg = build_strategy(strategy, seed=0)
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=extractor,
        writer=writer,
        meter=meter,
        budget_haiku_eq=20_000.0,
    )

    loaded = load_bundle(bundle.root)
    assert loaded.pages, "no pages were produced"

    # M1: coverage residual
    chunk_texts = [p.body_clean for p in loaded.pages][:5]
    chunk_embeds = embed_texts(chunk_texts)
    f = coverage_residual(loaded, chunk_embeds, embed_texts)
    assert 0.0 <= f <= 2.0

    # M3: graph crystallinity
    m3 = spectral_gap_modularity(loaded)
    assert "modularity" in m3 and "spectral_gap" in m3

    # M5: hit rate (run_meta has chunks_read)
    h = hit_rate(loaded)
    assert h != h or 0.0 <= h <= 1.0  # NaN allowed if no chunks recorded

    # M6 grounding gate (need chunk text)
    chunks_text: dict[str, str] = {}
    from wikify_simple.store.corpus import all_chunks

    for c in all_chunks(corpus):
        chunks_text[c.id] = c.text
    g = grounding(loaded, chunk_text=lambda cid: chunks_text.get(cid))
    # the fake quotes are first sentences of chunks; gate may not pass but
    # the metric must compute without error
    assert 0.0 <= g.g1_anchoring <= 1.0
    assert 0.0 <= g.g2_evidence_ok <= 1.0

    # GT-P
    rp = person_recall(loaded, ["Akira Fujishima", "Tuomo Suntola"])
    assert 0.0 <= rp <= 1.0

    # GT-C
    topics = ["photocatalysis", "atomic layer deposition"]
    topic_embeds = embed_texts(topics)
    rc = concept_recall(loaded, topics, topic_embeds, embed_texts)
    assert 0.0 <= rc <= 1.0


def test_heaps_over_seeds(corpus, tmp_path):
    """M2: feed three bundles into heaps_exponent."""
    from .fakes import FakeExtractor, FakeWriter

    bundles = []
    for i, budget in enumerate([5_000.0, 10_000.0, 20_000.0]):
        bundle = BundlePaths(root=tmp_path / f"M_b{i}")
        cache = ExtractCache(root=tmp_path / f"cache_{i}")
        meter = CostMeter(budget_haiku_eq=budget, run_id=f"M_b{i}", events_path=bundle.calls_path)
        cfg = build_strategy("M", seed=i)
        pipeline_run(
            corpus=corpus,
            bundle=bundle,
            strategy=cfg,
            extractor=FakeExtractor(cache, meter),
            writer=FakeWriter(meter),
            meter=meter,
            budget_haiku_eq=budget,
        )
        loaded = load_bundle(bundle.root)
        loaded.run_meta["cost_haiku_eq"] = budget
        bundles.append(loaded)
    fit = heaps_exponent(bundles, cost_of=lambda b: b.run_meta["cost_haiku_eq"])
    assert len(fit.costs) == 3
