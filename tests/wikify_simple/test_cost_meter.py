"""Token-based cost accounting: figures payload must move the meter."""

from __future__ import annotations

from wikify_simple.agents.schema import ImageRef, WriteEvidenceRef, WriteRequest
from wikify_simple.bindings.fake import FakeWriter
from wikify_simple.infra.cost_meter import CostMeter, TierPrice


def _meter(tmp_path) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=1_000_000.0,
        run_id="test",
        events_path=tmp_path / "events.jsonl",
    )


def _write_req(n_figures: int) -> WriteRequest:
    return WriteRequest(
        page_id="concept-x",
        page_kind="concept",
        title="X",
        aliases=[],
        skeleton="",
        evidence=[
            WriteEvidenceRef(chunk_id="d/0", doc_id="d", quote="q", locator=""),
        ],
        neighbor_titles=[],
        prompt_template="wikify_simple/write/v1",
        model_id="haiku",
        tier="L",
        figures=[
            ImageRef(id=f"d/f{i}", label=f"F{i}", caption="c", page=1, path=f"p{i}.png")
            for i in range(n_figures)
        ],
    )


def test_write_cost_scales_with_figures(tmp_path):
    m0 = _meter(tmp_path / "a")
    FakeWriter(m0).write(_write_req(0))
    spent0 = m0.spent_haiku_eq

    m14 = _meter(tmp_path / "b")
    FakeWriter(m14).write(_write_req(14))
    spent14 = m14.spent_haiku_eq

    # each figure adds 50 input tokens at tier L input rate 60 per-m:
    # 14 * 50 * 60 = 42000 extra heq.
    delta = spent14 - spent0
    assert delta > 40_000, f"figures payload barely moved cost: {delta}"
    assert delta < 50_000, f"figures payload moved cost too much: {delta}"


def test_tier_overhead_nonzero(tmp_path):
    # baseline: S tier should cost more than bare token math because of
    # the per-call fixed overhead.
    tier = TierPrice(name="S", input_per_m=1.0, output_per_m=1.0, fixed_overhead=50.0)
    assert tier.haiku_eq(0, 0) == 50.0
    assert tier.haiku_eq(100, 50) == 100 + 50 + 50


def test_tier_ordering(tmp_path):
    s = TierPrice(name="S", input_per_m=1.0, output_per_m=1.0, fixed_overhead=50.0)
    m_ = TierPrice(name="M", input_per_m=12.0, output_per_m=15.0, fixed_overhead=200.0)
    lg = TierPrice(name="L", input_per_m=60.0, output_per_m=75.0, fixed_overhead=500.0)
    assert s.haiku_eq(1000, 200) < m_.haiku_eq(1000, 200) < lg.haiku_eq(1000, 200)
