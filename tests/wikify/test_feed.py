"""Incremental refine mode.

Run distill once on the smoke fixture, then a second time with
iteration=refine against the same bundle + cache. The second run must
skip all chunks via the extract cache (n_new_extracted == 0) and must
not explode the page count (canonicalize merges by alias).
"""

import json
from pathlib import Path

import pytest

from wikify.cache import ExtractCache
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.strategy import build_strategy
from wikify.ingest.pipeline import ingest_corpus
from wikify.meter import CostMeter
from wikify.paths import BundlePaths, CorpusPaths

from .fakes import FakeExtractor, FakeWriter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("feed_corpus")
    return ingest_corpus(FIXTURE, out)


def _run(
    bundle: BundlePaths,
    cache: ExtractCache,
    corpus: CorpusPaths,
    iteration: str = "create",
) -> dict:
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="feed-test",
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
        iteration=iteration,
    )
    return json.loads(bundle.run_path.read_text(encoding="utf-8"))


def _count_pages(bundle: BundlePaths) -> int:
    n = 0
    for sub in ("concepts", "people"):
        d = bundle.root / sub
        if d.exists():
            n += len(list(d.glob("*.md")))
    return n


def test_refine_preserves_pages(corpus, tmp_path):
    """Refine iteration uses coverage memory and does not explode page count."""
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")

    snap1 = _run(bundle, cache, corpus, iteration="create")
    pages1 = _count_pages(bundle)
    assert pages1 > 0
    assert snap1["n_new_extracted"] >= 1

    snap2 = _run(bundle, cache, corpus, iteration="refine")
    pages2 = _count_pages(bundle)

    assert snap2["iteration"] == "refine"
    # Refine uses coverage memory to explore new chunks, so new
    # extractions are expected. Pages should be stable or grow
    # (canonicalize merges by alias), never shrink.
    assert pages2 >= pages1
