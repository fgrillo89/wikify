"""Token-based cost accounting: figures payload must move the meter."""

from wikify.meter import CostMeter, TierPrice
from wikify.schema import ImageRef, WriteEvidenceRef, WriteRequest

from .fakes import FakeWriter


def _meter(tmp_path) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=1_000_000.0,
        run_id="test",
        events_path=tmp_path / "events.jsonl",
    )


def _write_req(n_figures: int) -> WriteRequest:
    return WriteRequest(
        page_id="concept-x",
        page_kind="article",
        title="X",
        aliases=[],
        skeleton="",
        evidence=[
            WriteEvidenceRef(chunk_id="d/0", doc_id="d", quote="q", locator=""),
        ],
        prompt_template="wikify/write",
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

    # Each figure adds 50 input tokens at tier L input rate 15 per token:
    # 14 * 50 * 15 = 10_500 extra heq.
    delta = spent14 - spent0
    assert delta > 9_000, f"figures payload barely moved cost: {delta}"
    assert delta < 12_000, f"figures payload moved cost too much: {delta}"


def test_tier_overhead_nonzero(tmp_path):
    # baseline: S tier should cost more than bare token math because of
    # the per-call fixed overhead.
    tier = TierPrice(name="S", input_per_m=1.0, output_per_m=5.0, fixed_overhead=50.0)
    assert tier.haiku_eq(0, 0) == 50.0
    assert tier.haiku_eq(100, 50) == 100 + 250 + 50


def test_writer_tier_m_is_cheaper_than_l(tmp_path):
    """M (sonnet) must be noticeably cheaper than L (opus).

    With Claude-4 ratios (S=1/5, M=3/15, L=15/75), a 1000-in/120-out write:
      L: 1000*15 + 120*75 + 300 = 24_300 heq
      M: 1000*3  + 120*15 + 100 = 4_900 heq
    """
    lg = TierPrice(name="L", input_per_m=15.0, output_per_m=75.0, fixed_overhead=300.0)
    m_ = TierPrice(name="M", input_per_m=3.0, output_per_m=15.0, fixed_overhead=100.0)
    cost_l = lg.haiku_eq(1000, 120)
    cost_m = m_.haiku_eq(1000, 120)
    assert abs(cost_l - 24_300.0) < 1.0
    assert abs(cost_m - 4_900.0) < 1.0
    assert cost_l / cost_m >= 4.5


def test_mixed_strategy_uses_tier_m_for_writer():
    """The headline mixed strategy must use tier M for the writer.

    Guards against a silent bump back to L.
    """
    from wikify.distill.strategy import build_strategy

    cfg = build_strategy("balanced")
    assert cfg.write_tier == "M"


def test_tier_ordering(tmp_path):
    s = TierPrice(name="S", input_per_m=1.0, output_per_m=5.0, fixed_overhead=50.0)
    m_ = TierPrice(name="M", input_per_m=3.0, output_per_m=15.0, fixed_overhead=100.0)
    lg = TierPrice(name="L", input_per_m=15.0, output_per_m=75.0, fixed_overhead=300.0)
    assert s.haiku_eq(1000, 200) < m_.haiku_eq(1000, 200) < lg.haiku_eq(1000, 200)
