"""Eventual coverage scheduler tests."""

from __future__ import annotations

from wikify.wiki.discovery.contracts import (
    CoverageRecord,
    ExtractionUnit,
    ModalityKind,
    UnitKind,
)
from wikify.wiki.discovery.scheduler import EventualCoverageScheduler


def _unit(uid: str, weight: float = 1.0) -> ExtractionUnit:
    return ExtractionUnit(
        unit_id=uid,
        document_id="d",
        kind=UnitKind.CHUNK,
        modality=ModalityKind.TEXT,
        payload="",
        weight=weight,
    )


def test_scheduler_eventually_covers_all_units():
    units = [_unit(f"u{i}") for i in range(25)]
    coverage = CoverageRecord(document_id="d", strategy_id="s")
    sched = EventualCoverageScheduler(budget=10)

    epochs = 0
    while True:
        decision = sched.select(units, coverage)
        if not decision.selected:
            break
        for u in decision.selected:
            coverage.mark_processed(u.unit_id)
        epochs += 1
        if epochs > 10:
            break

    assert coverage.processed_unit_ids == {u.unit_id for u in units}
    assert epochs == sched.epochs_to_full_coverage(len(units)) == 3


def test_scheduler_prioritizes_by_weight():
    units = [_unit("low", weight=0.1), _unit("hi", weight=10.0), _unit("mid", weight=1.0)]
    coverage = CoverageRecord(document_id="d", strategy_id="s")
    decision = EventualCoverageScheduler(budget=2).select(units, coverage)
    assert [u.unit_id for u in decision.selected] == ["hi", "mid"]
    assert [u.unit_id for u in decision.deferred] == ["low"]


def test_scheduler_skips_processed_units():
    units = [_unit("a"), _unit("b")]
    coverage = CoverageRecord(document_id="d", strategy_id="s")
    coverage.mark_processed("a")
    decision = EventualCoverageScheduler(budget=10).select(units, coverage)
    assert [u.unit_id for u in decision.selected] == ["b"]
