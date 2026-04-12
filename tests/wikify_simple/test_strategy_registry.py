"""Strategy configs are data rows consumed by one factory."""

from wikify_simple.distill.sampler import GlobalOp, LevyMixSampler, LocalOp
from wikify_simple.distill.schedule import AdaptiveSchedule, StaticSchedule
from wikify_simple.distill.strategies import (
    STRATEGY_CONFIGS,
    StrategyId,
    build_strategy,
)


def test_strategy_registry_covers_every_enum_value():
    assert set(STRATEGY_CONFIGS) == {strategy.value for strategy in StrategyId}


def test_build_strategy_instantiates_mixed_from_config():
    cfg = build_strategy(StrategyId.MIXED, seed=7)
    assert cfg.name == "M"
    assert cfg.seed == 7
    assert cfg.extract_tier == "S"
    assert cfg.write_tier == "M"
    assert isinstance(cfg.sampler, LevyMixSampler)
    assert cfg.sampler.local_op is LocalOp.SIMILARITY_WALK
    assert cfg.sampler.global_op is GlobalOp.COVERAGE_GAP
    assert cfg.sampler.jump_rate == 0.1
    assert isinstance(cfg.schedule, AdaptiveSchedule)
    assert cfg.schedule.exploit_fraction_initial == 0.65


def test_build_strategy_accepts_string_ids():
    cfg = build_strategy("X", seed=3)
    assert cfg.name == "X"
    assert cfg.seed == 3
    assert cfg.extract_tier == "M"
    assert isinstance(cfg.schedule, StaticSchedule)
    assert cfg.sampler.local_op is LocalOp.SIMILARITY_WALK
    assert cfg.sampler.global_op is GlobalOp.UNIFORM
    assert cfg.sampler.jump_rate == 0.0
