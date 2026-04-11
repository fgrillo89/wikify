"""Budget schedule: how to split total budget across explore vs exploit.

Static splits divide once up front. Adaptive splits re-tune mid-run from
the Heaps slope dN/dC: when novelty drops below threshold, the remaining
budget shifts toward write.
"""

from dataclasses import dataclass
from typing import Protocol

from .config import CURATE_FRACTION, NOVELTY_THRESHOLD


@dataclass(frozen=True)
class BudgetSplit:
    extract_haiku_eq: float
    write_haiku_eq: float
    curate_haiku_eq: float


class Schedule(Protocol):
    def initial_split(self, total: float) -> BudgetSplit: ...
    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit: ...


@dataclass(frozen=True)
class StaticSchedule(Schedule):
    exploit_fraction: float

    def initial_split(self, total: float) -> BudgetSplit:
        curate = CURATE_FRACTION * total
        exploit = self.exploit_fraction * total
        explore = max(total - curate - exploit, 0.0)
        return BudgetSplit(extract_haiku_eq=explore, write_haiku_eq=exploit, curate_haiku_eq=curate)

    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit:
        return self.initial_split(remaining)


@dataclass(frozen=True)
class AdaptiveSchedule(Schedule):
    exploit_fraction_initial: float
    novelty_threshold: float = NOVELTY_THRESHOLD

    def initial_split(self, total: float) -> BudgetSplit:
        return StaticSchedule(self.exploit_fraction_initial).initial_split(total)

    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit:
        ef = self.exploit_fraction_initial
        if novelty_rate < self.novelty_threshold:
            ef = max(ef, 0.7)
        return StaticSchedule(ef).initial_split(remaining)
