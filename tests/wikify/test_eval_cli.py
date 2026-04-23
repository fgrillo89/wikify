"""Tests for the `wikify eval` CLI verb."""

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cache import ExtractCache
from wikify.cli import app
from wikify.distill.pipeline import run as pipeline_run
from wikify.distill.strategy import build_strategy
from wikify.ingest.pipeline import ingest_corpus
from wikify.meter import CostMeter
from wikify.paths import BundlePaths

from .fakes import FakeExtractor, FakeWriter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


def test_eval_cli_writes_report(tmp_path):
    corpus = ingest_corpus(FIXTURE, tmp_path / "corpus")
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="M_1x_seed0",
        events_path=bundle.calls_path,
    )
    cfg = build_strategy("balanced", seed=0)
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=FakeWriter(meter),
        meter=meter,
        budget_haiku_eq=20_000.0,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "--bundle",
            str(bundle.root),
            "--corpus",
            str(corpus.root),
        ],
    )
    assert result.exit_code == 0, result.output

    report = bundle.root / "_metrics.md"
    sidecar = bundle.root / "_metrics.json"
    assert report.exists()
    assert sidecar.exists()

    text = report.read_text(encoding="utf-8")
    for section in (
        "M1 — coverage residual",
        "M3 — g_evidence",
        "M3 — g_links",
        "M5 — hit rate",
        "M6 — grounding",
    ):
        assert section in text, f"missing section: {section}"

    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "M1_coverage_residual" in payload
    assert "M3_g_evidence" in payload
    assert "M6_grounding" in payload
    assert payload["embedder"]["backend"] in {"hash", "fastembed", "sentence_transformers"}
