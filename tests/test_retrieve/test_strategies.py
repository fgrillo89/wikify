"""Tests for the retrieval strategy registry and metadata.

Does NOT test retrieve() methods — those require a live DB and ChromaDB.
Only tests the registry, StrategyConfig defaults, and class-level metadata.
"""

from __future__ import annotations

import pytest

from scholarforge.retrieve.strategies import (
    FlatStrategy,
    HubAndSpokeStrategy,
    StrategyConfig,
    get_strategy,
    list_strategies,
)


# ── Registry ──────────────────────────────────────────────────────────────────


def test_get_strategy_flat_returns_flat_instance():
    strategy = get_strategy("flat")
    assert isinstance(strategy, FlatStrategy)


def test_get_strategy_hub_spoke_returns_hub_and_spoke_instance():
    strategy = get_strategy("hub-spoke")
    assert isinstance(strategy, HubAndSpokeStrategy)


def test_get_strategy_unknown_raises_value_error():
    with pytest.raises(ValueError, match="unknown"):
        get_strategy("unknown")


def test_list_strategies_returns_all_five():
    strategies = list_strategies()
    assert len(strategies) == 5


def test_list_strategies_contains_expected_names():
    names = {s["name"] for s in list_strategies()}
    assert names == {"flat", "hub-spoke", "topic-cluster", "query-driven", "snowball"}


# ── StrategyConfig defaults ───────────────────────────────────────────────────


def test_strategy_config_default_token_budget():
    config = StrategyConfig()
    assert config.token_budget == 12_000


def test_strategy_config_default_deep_read_limit():
    config = StrategyConfig()
    assert config.deep_read_limit == 3


# ── Class-level metadata ──────────────────────────────────────────────────────


def test_flat_strategy_expensive_is_false():
    assert FlatStrategy.expensive is False


def test_hub_and_spoke_strategy_expensive_is_true():
    assert HubAndSpokeStrategy.expensive is True


# ── estimate_cost ─────────────────────────────────────────────────────────────


def test_hub_and_spoke_estimate_cost_returns_expected_keys():
    strategy = get_strategy("hub-spoke")
    cost = strategy.estimate_cost()
    assert "llm_calls" in cost
    assert "est_usd" in cost


def test_hub_and_spoke_estimate_cost_values_are_numeric():
    strategy = get_strategy("hub-spoke")
    cost = strategy.estimate_cost()
    assert isinstance(cost["llm_calls"], (int, float))
    assert isinstance(cost["est_usd"], float)


def test_hub_and_spoke_estimate_cost_llm_calls_matches_config():
    config = StrategyConfig(deep_read_limit=3)
    strategy = get_strategy("hub-spoke", config=config)
    cost = strategy.estimate_cost()
    # n_hubs = deep_read_limit + 1
    assert cost["llm_calls"] == config.deep_read_limit + 1


def test_flat_strategy_estimate_cost_no_llm_calls():
    strategy = get_strategy("flat")
    cost = strategy.estimate_cost()
    assert cost["llm_calls"] == 0
    assert cost["est_usd"] == 0.0
