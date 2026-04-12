"""Query mode tests: fake binding, non-mutation, citations present."""

import time
from pathlib import Path

import pytest

from .fakes import FakeExtractor, FakeQuerier, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.query import run as query_run
from wikify_simple.distill.strategy import build_strategy
from wikify_simple.cache import ExtractCache
from wikify_simple.meter import CostMeter
from wikify_simple.embedding import embed_texts
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths, CorpusPaths

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


def test_query_cli_writes_md(tmp_path, ready_bundle, monkeypatch):
    bundle, corpus = ready_bundle
    monkeypatch.setenv("WIKIFY_SIMPLE_ALLOW_NETWORK", "1")
    from typer.testing import CliRunner

    from wikify_simple.cli import app

    runner = CliRunner()
    out_root = tmp_path / "queries_out"
    result = runner.invoke(
        app,
        [
            "query",
            "what is photocatalysis?",
            "--bundle",
            str(bundle.root),
            "--corpus",
            str(corpus.root),
            "--out",
            str(out_root),
        ],
    )
    assert result.exit_code == 0, result.output
    bundle_out = out_root / bundle.root.name
    assert bundle_out.exists()
    mds = list(bundle_out.glob("*.md"))
    assert mds, "no query .md written"
    text = mds[0].read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "question:" in text
    assert "citations:" in text
