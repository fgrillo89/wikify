"""Strategy configs are data rows consumed by one factory."""

from wikify.distill.explorer import GlobalOp, LevyExplorer, LocalOp
from wikify.distill.strategy import (
    FULL_TOOLS,
    NAVIGATE_TOOLS,
    PRESET_CONFIGS,
    STRATEGY_CONFIGS,
    AdaptiveBudget,
    StaticBudget,
    StrategyId,
    build_preset,
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


# ---- preset tests -------------------------------------------------------


def test_preset_configs_cover_five_conditions():
    expected = {
        "scripted-explore", "scripted-mixed", "scripted-exploit",
        "guided-navigate", "guided-full",
    }
    assert set(PRESET_CONFIGS) == expected


def test_build_preset_scripted_mixed():
    p = build_preset("scripted-mixed", seed=5)
    assert p.mode == "scripted"
    assert p.allowed_tools is None
    assert p.strategy.name == "M"
    assert p.strategy.seed == 5


def test_build_preset_guided_navigate():
    p = build_preset("guided-navigate")
    assert p.mode == "guided"
    assert p.allowed_tools == NAVIGATE_TOOLS
    assert "sample_chunks" in p.allowed_tools
    assert "write_now" in p.allowed_tools
    assert "done" not in p.allowed_tools
    assert "set_allocation" not in p.allowed_tools


def test_build_preset_guided_full():
    p = build_preset("guided-full")
    assert p.mode == "guided"
    assert p.allowed_tools == FULL_TOOLS
    assert "done" in p.allowed_tools
    assert "set_allocation" in p.allowed_tools


def test_navigate_tools_subset_of_full_tools():
    assert NAVIGATE_TOOLS < FULL_TOOLS
