"""Strategy configs are data rows consumed by one factory."""

from wikify.distill.explorer import GlobalOp, LevyExplorer, LocalOp
from wikify.distill.strategy import (
    STRATEGY_CONFIGS,
    AdaptiveBudget,
    StaticBudget,
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
    assert isinstance(cfg.explorer, LevyExplorer)
    assert cfg.explorer.local_op is LocalOp.SIMILARITY_WALK
    assert cfg.explorer.global_op is GlobalOp.COVERAGE_GAP
    assert cfg.explorer.jump_rate == 0.1
    assert isinstance(cfg.budget, AdaptiveBudget)
    assert cfg.budget.exploit_fraction_initial == 0.65


def test_build_strategy_accepts_string_ids():
    cfg = build_strategy("X", seed=3)
    assert cfg.name == "X"
    assert cfg.seed == 3
    assert cfg.extract_tier == "M"
    assert isinstance(cfg.budget, StaticBudget)
    assert cfg.explorer.local_op is LocalOp.SIMILARITY_WALK
    assert cfg.explorer.global_op is GlobalOp.UNIFORM
    assert cfg.explorer.jump_rate == 0.0
