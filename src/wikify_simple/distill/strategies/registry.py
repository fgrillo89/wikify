"""Strategy config registry and factory."""

from dataclasses import dataclass, replace
from enum import Enum

from ...contracts.tiers import ModelTier
from ..sampler import GlobalOp, LevyMixSampler, LocalOp, Sampler
from ..schedule import AdaptiveSchedule, Schedule, StaticSchedule


class StrategyId(str, Enum):
    EXPLORE = "E"
    MIXED = "M"
    EXPLOIT = "X"


@dataclass
class StrategyConfig:
    name: str
    sampler: Sampler
    schedule: Schedule
    extract_tier: ModelTier
    write_tier: ModelTier
    edit_tier: ModelTier = ModelTier.MEDIUM
    compact_tier: ModelTier = ModelTier.SMALL
    orchestrate_tier: ModelTier = ModelTier.LARGE
    # Allocation override. When not None, replaces the schedule's
    # exploit_fraction for the initial split. The LLM policy can still
    # mutate the allocation mid-run via set_allocation actions.
    exploit_fraction_override: float | None = None
    seed: int = 0
    field_name: str = "generic"
    artifact_name: str = "wiki_article"
    policy_name: str = "rule_policy"


STRATEGY_CONFIGS: dict[str, StrategyConfig] = {
    StrategyId.EXPLORE.value: StrategyConfig(
        name="E",
        sampler=LevyMixSampler(
            local_op=LocalOp.NONE,
            global_op=GlobalOp.PAGERANK,
            jump_rate=1.0,
        ),
        schedule=StaticSchedule(exploit_fraction=0.2),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.SMALL,
    ),
    StrategyId.MIXED.value: StrategyConfig(
        name="M",
        sampler=LevyMixSampler(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        schedule=AdaptiveSchedule(exploit_fraction_initial=0.65),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.MEDIUM,
    ),
    StrategyId.EXPLOIT.value: StrategyConfig(
        name="X",
        sampler=LevyMixSampler(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.UNIFORM,  # never used: jump_rate=0
            jump_rate=0.0,
        ),
        schedule=StaticSchedule(exploit_fraction=0.6),
        extract_tier=ModelTier.MEDIUM,
        write_tier=ModelTier.MEDIUM,
    ),
}


def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return replace(STRATEGY_CONFIGS[key], seed=seed)
