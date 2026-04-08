"""Tests for eval.metrics extras (g_links_modularity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify_simple.bindings.fake import FakeExtractor, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategies import STRATEGIES
from wikify_simple.eval.bundle import load_bundle
from wikify_simple.eval.metrics import g_links_modularity
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture
def loaded_bundle(tmp_path):
    corpus = ingest_corpus(FIXTURE, tmp_path / "corpus")
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="M_1x_seed0",
        events_path=bundle.calls_path,
    )
    cfg = STRATEGIES["M"](seed=0)
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=20_000.0,
    )
    return load_bundle(bundle.root)


def test_g_links_modularity_shape(loaded_bundle):
    out = g_links_modularity(loaded_bundle)
    assert set(out.keys()) == {"modularity", "spectral_gap", "n_nodes", "n_edges"}
    assert isinstance(out["modularity"], float)
    assert isinstance(out["spectral_gap"], float)
    assert isinstance(out["n_nodes"], float)
    assert isinstance(out["n_edges"], float)
