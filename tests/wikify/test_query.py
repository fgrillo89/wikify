"""Query mode tests: fake binding, non-mutation, citations present."""

import time
from pathlib import Path

import pytest

from .fakes import FakeExtractor, FakeQuerier, FakeWriter
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.query import run as query_run
from wikify.distill.strategy import build_strategy
from wikify.cache import ExtractCache
from wikify.meter import CostMeter
from wikify.embedding import embed_texts
from wikify.ingest.pipeline import ingest_corpus
from wikify.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


def _snapshot_mtimes(root: Path) -> dict:
    out = {}
    for p in root.rglob("*"):
        try:
            out[str(p)] = p.stat().st_mtime_ns
        except FileNotFoundError:
            pass
    return out


@pytest.fixture(scope="module")
def ready_bundle(tmp_path_factory) -> tuple[BundlePaths, CorpusPaths]:
    root = tmp_path_factory.mktemp("query")
    corpus = ingest_corpus(FIXTURE, root / "corpus")
    bundle = BundlePaths(root=root / "bundle")
    cache = ExtractCache(root=root / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="Q_1x_seed0",
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
    return bundle, corpus


@pytest.mark.parametrize(
    "question",
    [
        "what is photocatalysis?",
        "what is ALD?",
        "what is water splitting?",
    ],
)
def test_query_returns_answer_without_mutation(ready_bundle, tmp_path, question):
    bundle, corpus = ready_bundle
    before = _snapshot_mtimes(bundle.root)
    t0 = time.monotonic()
    answer = query_run(
        bundle=bundle,
        corpus=corpus,
        question=question,
        querier=FakeQuerier(),
        embed=embed_texts,
        cache_root=tmp_path / "qcache",
        save_log=False,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0
    assert isinstance(answer.text, str) and answer.text
    assert isinstance(answer.citations, list)
    after = _snapshot_mtimes(bundle.root)
    assert before == after, "query mutated the bundle"


