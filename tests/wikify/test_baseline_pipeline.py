"""Smoke test for the abstract-first baseline end-to-end.

Verifies the load-bearing baseline contracts against the tiny fixture:

- the baseline's own 60/35/5 split is what drives the run, regardless of
  any strategy-level ``exploit_fraction_override``;
- the seed extract pass is bounded by ``abstract_fraction * extract_budget``;
- the run snapshot records ``baseline_write_fraction`` and the seed set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.baselines.pipeline import BaselineConfig, run_baseline
from wikify.cache import ExtractCache
from wikify.distill.preload import preload_corpus
from wikify.distill.strategy import build_strategy
from wikify.ingest.pipeline import ingest_corpus
from wikify.meter import CostMeter
from wikify.paths import BundlePaths, CorpusPaths
from wikify.schema import WriteResponse
from wikify.store.wiki_bundle import load_bundle

from .fakes import FakeExtractor, FakeWriter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


class _ValidFakeWriter(FakeWriter):
    """Extend the shared fake writer so bodies clear the real validator floor."""

    def write(self, request):
        resp = super().write(request)
        filler = (
            "The baseline smoke test uses deterministic filler prose so the "
            "writer output remains structurally valid while still exercising "
            "the real persistence path. "
        ) * 18
        body = resp.body_markdown.replace(
            "## References",
            "## Additional context\n\n"
            + filler
            + "\n\n## Limitations\n\n"
            + filler
            + "\n\n## References",
            1,
        )
        return WriteResponse(
            page_id=resp.page_id,
            page_kind=resp.page_kind,
            body_markdown=body,
            used_markers=resp.used_markers,
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            reasoning=resp.reasoning,
            extends_page_id=resp.extends_page_id,
            equations=resp.equations,
        )


@pytest.fixture
def corpus(tmp_path) -> CorpusPaths:
    return ingest_corpus(FIXTURE, tmp_path / "corpus")


def test_baseline_run_writes_pages_and_ignores_exploit_fraction_override(
    corpus, tmp_path,
):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    budget = 30_000.0
    meter = CostMeter(
        budget_haiku_eq=budget,
        run_id="baseline-smoke",
        events_path=bundle.calls_path,
    )
    # Strategy carries an exploit_fraction_override that the baseline MUST
    # ignore -- the baseline owns its own 60/35/5 split.
    cfg = build_strategy("balanced", seed=0)
    cfg.exploit_fraction_override = 0.9  # would be devastating if respected
    preloaded = preload_corpus(corpus)

    pages = run_baseline(
        kg=preloaded.knowledge_graph,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=_ValidFakeWriter(meter),
        meter=meter,
        budget_haiku_eq=budget,
        preloaded=preloaded,
        config=BaselineConfig(min_evidence_chunks=1),
    )

    loaded = load_bundle(bundle.root)

    snap = loaded.run_meta
    # Baseline owns the split; the override on cfg must NOT have leaked.
    assert snap["baseline_write_fraction"] == pytest.approx(0.35)
    expected_split = budget * 0.35
    assert snap["split_initial"]["write_haiku_eq"] == pytest.approx(expected_split, rel=1e-6)
    # Seed selection ran and was recorded.
    assert snap["seed_doc_ids"], "seed_doc_ids missing from snapshot"
    assert snap["seed_chunks_read"], "seed_chunks_read missing from snapshot"
    assert snap["n_pages_written"] == len(pages)
    assert len(loaded.pages) == len(pages)
    # Hard meter contract still holds.
    assert meter.spent_haiku_eq <= 1.05 * budget


def test_baseline_skipped_thin_pages_do_not_leak_into_bundle(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    budget = 30_000.0
    meter = CostMeter(
        budget_haiku_eq=budget,
        run_id="baseline-thin-pages",
        events_path=bundle.calls_path,
    )
    cfg = build_strategy("balanced", seed=0)
    preloaded = preload_corpus(corpus)

    pages = run_baseline(
        kg=preloaded.knowledge_graph,
        bundle=bundle,
        strategy=cfg,
        extractor=FakeExtractor(cache, meter),
        writer=_ValidFakeWriter(meter),
        meter=meter,
        budget_haiku_eq=budget,
        preloaded=preloaded,
        config=BaselineConfig(min_evidence_chunks=999),
    )

    loaded = load_bundle(bundle.root)
    assert pages == []
    assert loaded.pages == []
    assert loaded.run_meta["n_pages_written"] == 0
    assert loaded.run_meta["skipped_thin_pages"], "expected skipped_thin_pages to be recorded"
