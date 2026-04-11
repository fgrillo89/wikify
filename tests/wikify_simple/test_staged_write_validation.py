"""Staged write responses must be schema-validated before use."""

import json
from pathlib import Path

import pytest

from wikify_simple.bindings.fake import FakeExtractor, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategies import STRATEGIES
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture
def corpus(tmp_path) -> CorpusPaths:
    return ingest_corpus(FIXTURE, tmp_path / "corpus")


def _meter(bundle: BundlePaths, run_id: str) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=40_000.0,
        run_id=run_id,
        events_path=bundle.calls_path,
    )


def test_invalid_staged_response_falls_back_to_writer(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    cfg = STRATEGIES["M"](seed=0)

    # Phase 1: extract to materialize _write_requests + _pages.json.
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, _meter(bundle, "extract-phase")),
        writer=FakeWriter(_meter(bundle, "extract-phase-w")),
        meter=_meter(bundle, "extract-phase-main"),
        budget_haiku_eq=40_000.0,
        phase="extract",
    )

    pages_manifest = bundle.write_requests_dir / "_pages.json"
    assert pages_manifest.exists(), "extract phase should emit _pages.json"
    pages = json.loads(pages_manifest.read_text(encoding="utf-8"))
    target_id = None
    for p in pages:
        evidence = p.get("evidence") or []
        if not evidence:
            continue
        if p.get("kind") == "person" and len(evidence) < 2:
            continue
        if p.get("kind") == "article":
            target_id = p["id"]
            break
    assert target_id is not None, "expected a writable concept page in _pages.json"
    req = bundle.write_requests_dir / f"{target_id}.request.json"
    assert req.exists(), "expected matching request JSON for selected page"
    bad_resp = req.with_name(req.name.replace(".request.", ".response."))
    bad_resp.write_text(
        json.dumps({"page_id": req.stem, "body_markdown": "too short"}),
        encoding="utf-8",
    )

    # Phase 2: write should reject bad staged JSON and call writer fallback.
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, _meter(bundle, "write-phase-e")),
        writer=FakeWriter(_meter(bundle, "write-phase-w")),
        meter=_meter(bundle, "write-phase-main"),
        budget_haiku_eq=40_000.0,
        phase="write",
        iteration="refine",
    )

    err = bad_resp.with_name(bad_resp.name.replace(".response.", ".error."))
    assert err.exists(), "invalid staged response must leave an .error.json artifact"

    concept_pages = sorted((bundle.root / "articles").glob("*.md"))
    assert concept_pages, "write fallback should still produce wiki pages"
    body = concept_pages[0].read_text(encoding="utf-8")
    assert "## References" in body
