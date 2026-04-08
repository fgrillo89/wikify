"""Cell M — Lévy + Bayesian-opt headline.

(similarity_walk, coverage_gap, 0.1) / (0.4, adaptive) / (S, M).
"""

from __future__ import annotations

from ..pipeline import StrategyConfig
from ..sampler import GlobalOp, LevyMixSampler, LocalOp
from ..schedule import AdaptiveSchedule


def build(seed: int = 0) -> StrategyConfig:
    return StrategyConfig(
        name="M",
        sampler=LevyMixSampler(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        schedule=AdaptiveSchedule(exploit_fraction_initial=0.4),
        tier_explore="S",
        tier_exploit="M",
        seed=seed,
    )
