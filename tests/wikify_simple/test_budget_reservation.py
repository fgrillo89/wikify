"""Phase 5C: writer budget reservation.

The extract loop must not consume the write reserve. The write loop must
pre-check each page against a 1.05x hard ceiling and record
``reason=budget_truncated`` for pages that would overshoot.

Scenario
--------
budget_haiku_eq = 50_000
schedule: exploit_fraction=0.6
  extract split = 17_500 heq (explore share)
  write split   = 30_000 heq (exploit share)
  expected_write_reserve = 30_000 * 0.95 = 28_500

extract cap = min(17_500, 50_000 - 28_500) = 17_500
Each fake extract call costs ~5_000 heq (tier S, tokens_in=800, tokens_out=830).
After ~3 extracts (~15_000 spent) the loop exits normally (next call would
exceed the cap).

Write pre-check: avg_write_cost starts at 30_000.
  First write:  15_000 + 30_000 = 45_000 <= 52_500 -> proceeds (~30_100 cost)
  Second write: 45_100 + 30_100 = 75_200  > 52_500 -> budget_truncated

Assertions:
  - meter.spent_haiku_eq <= 1.05 * budget_haiku_eq (52_500)
  - at least one write_rejections entry has reason="budget_truncated"
  - at least one write succeeded (extract did not consume the full budget)
"""

import json
import time
from pathlib import Path

import pytest

from wikify_simple.contracts.protocols import Writer
from wikify_simple.contracts.roles import Role, response_reserve, total_context
from wikify_simple.contracts.schema import WriteRequest, WriteResponse
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.sampler import GlobalOp, LevyMixSampler, LocalOp
from wikify_simple.distill.schedule import StaticSchedule
from wikify_simple.distill.strategies import StrategyConfig
from wikify_simple.infra.cache import CachedExtract, ExtractCache, ExtractCacheKey, prompt_hash
from wikify_simple.infra.cost_meter import CostMeter
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"

BUDGET = 50_000.0
# Tier S: tokens_in*1.0 + tokens_out*5.0 + 50 = 800 + 4_150 + 50 = 5_000
EXTRACT_TOKENS_IN = 800
EXTRACT_TOKENS_OUT = 830
# Tier M: tokens_in*3.0 + tokens_out*15.0 + 100 = 3_000 + 27_000 + 100 = 30_100
WRITE_TOKENS_IN = 1_000
WRITE_TOKENS_OUT = 1_800


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("reservation_corpus")
    return ingest_corpus(FIXTURE, out)


class _CostTunedExtractor:
    """Extractor that spends ~5_000 heq per call (tier S, tuned token counts)."""

    BINDING_NAME = "cost_tuned_fake"

    def __init__(self, cache: ExtractCache, meter: CostMeter) -> None:
        self._cache = cache
        self._meter = meter

    def extract(self, request):
        from wikify_simple.bindings.fake import _fake_extract_payload
        from wikify_simple.contracts.schema import ExtractedConcept, ExtractResponse

        key = ExtractCacheKey(
            binding_name=self.BINDING_NAME,
            model_id=request.model_id,
            prompt_hash=prompt_hash(request.prompt_template),
            chunk_id=request.chunk_id,
        )
        t0 = time.monotonic()

        def compute() -> CachedExtract:
            payload = _fake_extract_payload(request)
            return CachedExtract(
                payload=payload,
                tokens_in=EXTRACT_TOKENS_IN,
                tokens_out=EXTRACT_TOKENS_OUT,
            )

        entry, was_hit = self._cache.get_or_extract(key, compute)
        wall = time.monotonic() - t0
        self._meter.record(
            role=Role.EXTRACTOR,
            tier=request.tier,
            input_tokens=entry.tokens_in,
            output_tokens=entry.tokens_out,
            context_cap=total_context() - response_reserve(),
            wall_seconds=wall,
            cache_hit=was_hit,
            prompt_hash=key.prompt_hash,
        )
        payload = entry.payload
        concepts = [
            ExtractedConcept(
                title=c["title"],
                aliases=c["aliases"],
                kind=c["kind"],
                quote=c["quote"],
                category=c.get("category"),
            )
            for c in payload["concepts"]
        ]
        return ExtractResponse(
            chunk_id=payload["chunk_id"],
            concepts=concepts,
            tokens_in=entry.tokens_in,
            tokens_out=entry.tokens_out,
        )


class _CostTunedWriter(Writer):
    """Writer that spends ~30_100 heq per call (tier M, tuned token counts)."""

    def __init__(self, meter: CostMeter) -> None:
        self._meter = meter

    def write(self, request: WriteRequest) -> WriteResponse:
        t0 = time.monotonic()
        used = [f"e{i}" for i in range(1, len(request.evidence) + 1)]
        m1 = used[0]
        m_last = used[-1]
        # Inline evidence markers required by WriteResponse validator.
        body = (
            f"# {request.title}\n\n"
            f"## Definition\n\n"
            f"{request.title} is a placeholder for the budget reservation test[^{m1}].\n\n"
            f"## Background\n\n"
            f"Additional context grounded in evidence[^{m_last}].\n\n"
            f"## References\n\n"
            + "\n".join(
                f"[^{m}]: {ev.quote or 'supporting quote'} ({ev.doc_id})"
                for m, ev in zip(used, request.evidence, strict=False)
            )
            + "\n"
        )
        self._meter.record(
            role=Role.WRITER,
            tier=request.tier,
            input_tokens=WRITE_TOKENS_IN,
            output_tokens=WRITE_TOKENS_OUT,
            context_cap=total_context() - response_reserve(),
            wall_seconds=time.monotonic() - t0,
            cache_hit=False,
            prompt_hash=prompt_hash(request.prompt_template),
        )
        return WriteResponse(
            page_id=request.page_id,
            body_markdown=body,
            used_markers=used,
            tokens_in=WRITE_TOKENS_IN,
            tokens_out=WRITE_TOKENS_OUT,
        )


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        name="M",
        sampler=LevyMixSampler(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        schedule=StaticSchedule(exploit_fraction=0.6),
        extract_tier="S",
        write_tier="M",
        seed=42,
    )


def test_budget_reservation(corpus, tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=BUDGET,
        run_id="reservation-test",
        events_path=bundle.calls_path,
    )

    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=_strategy(),
        extractor=_CostTunedExtractor(cache, meter),
        writer=_CostTunedWriter(meter),
        meter=meter,
        budget_haiku_eq=BUDGET,
    )

    snap = json.loads(bundle.run_path.read_text(encoding="utf-8"))

    # 1. Total spend must not exceed 1.05x the budget.
    assert meter.spent_haiku_eq <= 1.05 * BUDGET, (
        f"spent {meter.spent_haiku_eq:.0f} > 1.05 * {BUDGET:.0f} = {1.05 * BUDGET:.0f}"
    )

    # 2. At least one page must have been skipped with reason=budget_truncated.
    rejections = snap.get("write_rejections", [])
    truncated = [r for r in rejections if r.get("reason") == "budget_truncated"]
    assert truncated, (
        f"Expected at least one budget_truncated rejection; got write_rejections={rejections}"
    )

    # 3. At least one write succeeded: the extract phase did not consume all
    #    budget, leaving room for the first write.
    writer_role_key = Role.WRITER.value
    writer_agg = snap.get("by_role", {}).get(writer_role_key, {})
    assert writer_agg.get("calls", 0) >= 1, (
        "Expected at least one successful write call; "
        f"writer_agg={writer_agg}, write_rejections={rejections}"
    )
