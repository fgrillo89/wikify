"""Cell E — breadth-first cheap floor: (none, pagerank, 1.0) / (0.2, static) / (S, S)."""

from __future__ import annotations

from ..pipeline import StrategyConfig
from ..sampler import GlobalOp, LevyMixSampler, LocalOp
from ..schedule import StaticSchedule


def build(seed: int = 0) -> StrategyConfig:
    return StrategyConfig(
        name="E",
        sampler=LevyMixSampler(
            local_op=LocalOp.NONE,
            global_op=GlobalOp.PAGERANK,
            jump_rate=1.0,
        ),
        schedule=StaticSchedule(exploit_fraction=0.2),
        tier_explore="S",
        tier_exploit="S",
        seed=seed,
    )
