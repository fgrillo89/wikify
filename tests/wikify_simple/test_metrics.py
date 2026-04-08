"""Tests for eval.metrics extras (g_links_modularity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify_simple.bindings.fake import FakeExtractor, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategies import STRATEGIES
from wikify_simple.eval.bundle import load_bundle
from wikify_simple.eval.metrics import (
    EmbedderMismatch,
    coverage_residual,
    g_links_modularity,
)
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter
from wikify_simple.infra.embedding import embedder_for
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths
from wikify_simple.store.vectors import load_vectors
from wikify_simple.store.vectors_meta import read_meta

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
    return corpus, load_bundle(bundle.root)


def test_g_links_modularity_shape(loaded_bundle):
    _corpus, bundle = loaded_bundle
    out = g_links_modularity(bundle)
    assert set(out.keys()) == {"modularity", "spectral_gap", "n_nodes", "n_edges"}
    assert isinstance(out["modularity"], float)
    assert isinstance(out["spectral_gap"], float)
    assert isinstance(out["n_nodes"], float)
    assert isinstance(out["n_edges"], float)


def test_vectors_meta_written(loaded_bundle):
    corpus, _bundle = loaded_bundle
    meta = read_meta(corpus.vectors_path)
    assert meta is not None
    assert meta.backend in {"hash", "sentence_transformers"}
    vs = load_vectors(corpus.vectors_path)
    assert vs.matrix.shape[1] == meta.dim


def test_coverage_residual_explicit_embedder(loaded_bundle):
    corpus, bundle = loaded_bundle
    vs = load_vectors(corpus.vectors_path)
    meta = read_meta(corpus.vectors_path)
    embed = embedder_for(meta.backend, meta.model)
    val = coverage_residual(bundle, vs.matrix, embed)
    assert isinstance(val, float)


def test_coverage_residual_corpus_path(loaded_bundle):
    corpus, bundle = loaded_bundle
    vs = load_vectors(corpus.vectors_path)
    val = coverage_residual(bundle, vs.matrix, corpus=corpus)
    assert isinstance(val, float)


def test_coverage_residual_dim_mismatch(loaded_bundle):
    import numpy as np

    corpus, bundle = loaded_bundle
    # fabricate chunk embeddings of the wrong dim
    bogus = np.zeros((3, 7), dtype=np.float32)
    with pytest.raises(EmbedderMismatch):
        coverage_residual(bundle, bogus, corpus=corpus)


def test_coverage_residual_requires_embed_or_corpus(loaded_bundle):
    import numpy as np

    _corpus, bundle = loaded_bundle
    with pytest.raises(EmbedderMismatch):
        coverage_residual(bundle, np.zeros((1, 4), dtype=np.float32))
