"""Tests for eval.metrics extras (g_links_modularity)."""

from pathlib import Path

import pytest

from .fakes import FakeExtractor, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategy import build_strategy
from wikify_simple.eval.bundle import load_bundle
from wikify_simple.eval.metrics import (
    EmbedderMismatch,
    coverage_residual,
    g_links_modularity,
)
from wikify_simple.cache import ExtractCache
from wikify_simple.meter import CostMeter
from wikify_simple.embedding import embedder_for
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths
from wikify_simple.store.vectors import load_vectors
from wikify_simple.store.vectors_meta import read_meta

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def loaded_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp("metrics")
    corpus = ingest_corpus(FIXTURE, root / "corpus")
    bundle = BundlePaths(root=root / "bundle")
    cache = ExtractCache(root=root / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="M_1x_seed0",
        events_path=bundle.calls_path,
    )
    cfg = build_strategy("M", seed=0)
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
    # Louvain returns a real number on the smoke bundle (no NaN sentinel).
    import math

    assert not math.isnan(out["modularity"])
    assert not math.isnan(out["spectral_gap"])


def test_vectors_meta_written(loaded_bundle):
    corpus, _bundle = loaded_bundle
    meta = read_meta(corpus.vectors_path)
    assert meta is not None
    assert meta.backend in {"hash", "fastembed", "sentence_transformers"}
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


def test_coverage_residual_clamped_on_identical_embeddings(loaded_bundle, monkeypatch):
    """Regression: residual must not return -1e-8 from float underflow."""
    import numpy as np

    from wikify_simple.eval import metrics as metrics_mod

    _corpus, bundle = loaded_bundle
    n_pages = len(bundle.pages)
    assert n_pages > 0
    dim = 8
    vec = np.ones((1, dim), dtype=np.float32)
    vec /= np.linalg.norm(vec, axis=1, keepdims=True)
    page_embeds = np.repeat(vec, n_pages, axis=0)
    ids = [p.id for p in bundle.pages]

    def fake_load_or_compute(_bundle, _pages, _embed):
        return ids, page_embeds

    monkeypatch.setattr(
        "wikify_simple.store.bundle_embeddings.load_or_compute",
        fake_load_or_compute,
    )

    chunk_embeds = np.repeat(vec, 3, axis=0)  # identical to pages
    val = metrics_mod.coverage_residual(bundle, chunk_embeds, embed=lambda xs: vec)
    assert val >= 0.0
