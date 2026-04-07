"""Retrieval strategy registry.

Strategies determine how papers and chunks are selected and assembled
into a RetrievedContext for the generation pipeline.
"""

from __future__ import annotations

from wikify.papers.retrieve.strategies.base import RetrievalStrategy, StrategyConfig
from wikify.papers.retrieve.strategies.flat import FlatStrategy
from wikify.papers.retrieve.strategies.hierarchical import HierarchicalStrategy
from wikify.papers.retrieve.strategies.hub_spoke import HubAndSpokeStrategy
from wikify.papers.retrieve.strategies.query_driven import QueryDrivenStrategy
from wikify.papers.retrieve.strategies.snowball import SnowballStrategy
from wikify.papers.retrieve.strategies.topic_cluster import TopicClusteredStrategy

STRATEGY_REGISTRY: dict[str, type[RetrievalStrategy]] = {
    "flat": FlatStrategy,
    "hub-spoke": HubAndSpokeStrategy,
    "topic-cluster": TopicClusteredStrategy,
    "query-driven": QueryDrivenStrategy,
    "snowball": SnowballStrategy,
    "hierarchical": HierarchicalStrategy,
}

DEFAULT_STRATEGY = "flat"


def get_strategy(name: str, config: StrategyConfig | None = None) -> RetrievalStrategy:
    """Look up a strategy by name and return an instance."""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(STRATEGY_REGISTRY)
        msg = f"Unknown strategy '{name}'. Available: {available}"
        raise ValueError(msg)
    return cls(config=config)


def list_strategies() -> list[dict[str, str | bool]]:
    """Return strategy metadata for CLI help."""
    return [
        {
            "name": name,
            "expensive": cls.expensive,
            "description": cls.description,
        }
        for name, cls in STRATEGY_REGISTRY.items()
    ]


__all__ = [
    "DEFAULT_STRATEGY",
    "STRATEGY_REGISTRY",
    "FlatStrategy",
    "HierarchicalStrategy",
    "HubAndSpokeStrategy",
    "QueryDrivenStrategy",
    "RetrievalStrategy",
    "SnowballStrategy",
    "StrategyConfig",
    "TopicClusteredStrategy",
    "get_strategy",
    "list_strategies",
]
