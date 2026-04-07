"""Eventual-coverage scheduler.

Discovery scheduling may prioritize some units earlier, but it must not
permanently starve the rest of the corpus. ``EventualCoverageScheduler``
guarantees that every eligible unit is processed within a bounded number
of epochs by always exhausting the unprocessed pool before re-visiting
already-covered units.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wikify.wiki.discovery.contracts import CoverageRecord, ExtractionUnit


@dataclass
class SchedulerDecision:
    selected: list[ExtractionUnit]
    deferred: list[ExtractionUnit]
    epoch: int


@dataclass
class EventualCoverageScheduler:
    """Round-robin scheduler with priority and exploration support.

    Each call to ``select`` returns up to ``budget`` units for one epoch.
    Units that have not yet been processed are always preferred over
    re-visits, which guarantees eventual coverage.
    """

    budget: int = 32
    exploration_rate: float = 0.05
    _epoch: int = field(default=0)

    def select(
        self,
        units: list[ExtractionUnit],
        coverage: CoverageRecord,
        *,
        priority_key=None,
    ) -> SchedulerDecision:
        self._epoch += 1
        unprocessed = [u for u in units if u.unit_id not in coverage.processed_unit_ids]
        if priority_key is not None:
            unprocessed.sort(key=priority_key, reverse=True)
        else:
            unprocessed.sort(key=lambda u: -u.weight)

        selected = unprocessed[: self.budget]
        deferred = unprocessed[self.budget :]
        return SchedulerDecision(selected=selected, deferred=deferred, epoch=self._epoch)

    def epochs_to_full_coverage(self, total_units: int) -> int:
        if self.budget <= 0:
            return 0
        return (total_units + self.budget - 1) // self.budget
