"""Cell M — Lévy + Bayesian-opt headline.

(similarity_walk, coverage_gap, 0.1) / (0.65, adaptive) / (S, M).

Exploit fraction bumped from 0.4 -> 0.65 after the 1x mvp20 run showed
writer starvation: even with the L->M re-tier (commit a3ef62b) each
write costs ~10-14k heq, but a 0.4 split only left ~20k for writes
(2 calls). 0.65 leaves ~32k → ~3-4 writes per 1x budget.
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
        schedule=AdaptiveSchedule(exploit_fraction_initial=0.65),
        tier_explore="S",
        tier_exploit="M",
        seed=seed,
    )
