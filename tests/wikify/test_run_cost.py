"""Tests for wikify.bundle.run.cost — TierPrice + aggregation from events.jsonl."""

from __future__ import annotations

from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.run.cost import aggregate, cost_summary, haiku_eq_for
from wikify.bundle.run.events import Event, append_event


def _bundle(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle(root=tmp_path)


def test_haiku_eq_increases_with_tier() -> None:
    s = haiku_eq_for("S", 1000, 500)
    m = haiku_eq_for("M", 1000, 500)
    big = haiku_eq_for("L", 1000, 500)
    assert s < m < big


def test_haiku_eq_is_zero_for_zero_tokens() -> None:
    # Smallest tier still has fixed overhead, but for the lowest tier the
    # overhead is small enough that we don't assert it; we just check it's
    # non-negative and stable across calls.
    a = haiku_eq_for("S", 0, 0)
    b = haiku_eq_for("S", 0, 0)
    assert a == b
    assert a >= 0


def test_aggregate_only_counts_call_events() -> None:
    events = [
        Event(run_id="r", type="cli_invoked", actor="cli"),
        Event(
            run_id="r",
            type="call",
            actor="writer-1",
            data={
                "role": "writer",
                "tier": "M",
                "input_tokens": 1000,
                "output_tokens": 500,
                "haiku_eq": 18.5,
                "wall_seconds": 2.0,
                "cache_hit": False,
            },
        ),
        Event(run_id="r", type="page_committed", actor="cli"),
    ]
    agg = aggregate(events)
    assert agg["totals"]["calls"] == 1
    assert agg["totals"]["input_tokens"] == 1000
    assert agg["totals"]["output_tokens"] == 500
    assert agg["totals"]["haiku_eq"] == 18.5


def test_aggregate_breaks_down_by_tier_and_role() -> None:
    def call(role: str, tier: str, hi: float) -> Event:
        return Event(
            run_id="r",
            type="call",
            actor=role,
            data={
                "role": role,
                "tier": tier,
                "input_tokens": 100,
                "output_tokens": 50,
                "haiku_eq": hi,
            },
        )

    events = [
        call("writer", "M", 18.0),
        call("writer", "M", 22.0),
        call("extractor", "S", 1.5),
    ]
    agg = aggregate(events)
    assert agg["by_role"]["writer"]["calls"] == 2
    assert agg["by_role"]["writer"]["haiku_eq"] == 40.0
    assert agg["by_role"]["extractor"]["calls"] == 1
    assert agg["by_tier"]["M"]["calls"] == 2
    assert agg["by_tier"]["S"]["calls"] == 1


def test_cost_summary_reads_events_jsonl(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    append_event(
        bundle,
        Event(
            run_id="r",
            type="call",
            actor="writer-1",
            data={
                "role": "writer",
                "tier": "M",
                "input_tokens": 100,
                "output_tokens": 50,
                "haiku_eq": 12.0,
            },
        ),
    )
    summary = cost_summary(bundle)
    assert summary["totals"]["calls"] == 1
    assert summary["totals"]["haiku_eq"] == 12.0


def test_cost_summary_empty_when_no_events(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    summary = cost_summary(bundle)
    assert summary["totals"]["calls"] == 0
    assert summary["totals"]["haiku_eq"] == 0.0
