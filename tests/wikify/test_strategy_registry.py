"""Strategy configs are data rows consumed by one factory.

The canonical study surface for the small-scale run is
``baseline / balanced / guided`` (see docs/distill-test-readiness.md).
The follow-on conditions (``high-exploration``, ``high-exploitation``,
``no-navigation``) will land as additional rows when implemented.
"""

from wikify.distill.explorer import GlobalOp, LocalOp
from wikify.distill.strategy import (
    CANONICAL_PRESETS,
    FULL_TOOLS,
    NAVIGATE_TOOLS,
    PRESET_CONFIGS,
    STRATEGY_CONFIGS,
    StaticBudget,
    StrategyId,
    build_preset,
    build_strategy,
)


def test_strategy_registry_is_only_balanced():
    assert set(STRATEGY_CONFIGS) == {"balanced"}
    assert set(StrategyId) == {StrategyId.BALANCED}


def test_balanced_uses_fixed_60_35_5_split():
    cfg = build_strategy("balanced", seed=0)
    assert cfg.name == "balanced"
    assert cfg.extract_tier == "S"
    assert cfg.write_tier == "M"
    assert cfg.edit_tier == "M"
    assert cfg.compact_tier == "S"
    assert isinstance(cfg.budget, StaticBudget)
    assert cfg.budget.exploit_fraction == 0.35
    assert cfg.explorer.local_op is LocalOp.SIMILARITY_WALK
    assert cfg.explorer.global_op is GlobalOp.COVERAGE_GAP
    assert cfg.explorer.jump_rate == 0.1


def test_canonical_presets_are_baseline_balanced_guided():
    assert CANONICAL_PRESETS == ("baseline", "balanced", "guided")
    assert set(PRESET_CONFIGS) == {"baseline", "balanced", "guided"}


def test_build_preset_balanced():
    p = build_preset("balanced", seed=5)
    assert p.mode == "scripted"
    assert p.allowed_tools is None
    assert p.strategy.name == "balanced"
    assert p.strategy.seed == 5


def test_build_preset_guided_uses_full_tools():
    p = build_preset("guided")
    assert p.mode == "guided"
    assert p.allowed_tools == FULL_TOOLS
    assert "set_allocation" in p.allowed_tools
    assert "write_now" in p.allowed_tools
    assert "done" in p.allowed_tools


def test_build_preset_baseline_routes_to_baseline_mode():
    p = build_preset("baseline")
    # Baseline is intercepted by the CLI and dispatched through
    # baselines/pipeline.py; the preset still carries the strategy
    # config so tier defaults stay consistent with ``balanced``.
    assert p.mode == "baseline"
    assert p.allowed_tools is None
    assert p.strategy.name == "balanced"


def test_navigate_tools_subset_of_full_tools():
    assert NAVIGATE_TOOLS < FULL_TOOLS
