"""Iteration history and coverage-memory persistence."""

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


@pytest.fixture
def corpus(tmp_path) -> CorpusPaths:
    # dedup_same_stem=False: the tiny fixture ships
    # sample.pdf/.docx/.pptx/.html with matching stems on purpose — each
    # carries different content to exercise per-format parsers. The new
    # default (True) would collapse them to sample.pdf and leave the
    # iteration-history test with a dossier too sparse to produce two
    # iterations.
    return ingest_corpus(FIXTURE, tmp_path / "corpus", dedup_same_stem=False)


def _run(
    *,
    bundle: BundlePaths,
    corpus: CorpusPaths,
    cache: ExtractCache,
    run_id: str,
    iteration: str,
) -> None:
    cfg = build_strategy("M", seed=0)
    meter = CostMeter(
        budget_haiku_eq=40_000.0,
        run_id=run_id,
        events_path=bundle.calls_path,
    )
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=40_000.0,
        iteration=iteration,  # type: ignore[arg-type]
    )


def test_create_refine_append_run_history_and_page_provenance(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")

    _run(bundle=bundle, corpus=corpus, cache=cache, run_id="iter-create", iteration="create")
    _run(bundle=bundle, corpus=corpus, cache=cache, run_id="iter-refine", iteration="refine")

    assert bundle.run_history_path.exists()
    rows = [
        json.loads(line)
        for line in bundle.run_history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 2
    assert rows[-2]["iteration"] == "create"
    assert rows[-1]["iteration"] == "refine"

    assert bundle.coverage_memory_path.exists()
    mem = json.loads(bundle.coverage_memory_path.read_text(encoding="utf-8"))
    assert mem["seen_chunks"], "coverage memory should persist seen chunks"

    sidecars = sorted(bundle.articles_dir.glob("*.provenance.json"))
    assert sidecars, "concept page provenance sidecars should exist"
    prov = json.loads(sidecars[0].read_text(encoding="utf-8"))
    history = prov.get("history", [])
    assert len(history) >= 2
    assert history[-1]["iteration"] == "refine"
