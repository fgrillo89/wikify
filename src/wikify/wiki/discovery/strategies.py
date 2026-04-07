"""Discovery strategy registry.

Strategies bundle a workflow id with the document types they target and
the budgets/model tier they expect. The planner uses this registry to map
a ``DocumentProfile`` to a strategy without hard-coding the choice into
orchestration code.
"""

from __future__ import annotations

from wikify.wiki.discovery.contracts import DiscoveryStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._by_id: dict[str, DiscoveryStrategy] = {}

    def register(self, strategy: DiscoveryStrategy) -> None:
        if strategy.strategy_id in self._by_id:
            raise ValueError(f"strategy already registered: {strategy.strategy_id}")
        self._by_id[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> DiscoveryStrategy:
        return self._by_id[strategy_id]

    def all(self) -> list[DiscoveryStrategy]:
        return list(self._by_id.values())

    def for_document_type(self, document_type: str) -> list[DiscoveryStrategy]:
        return [
            s
            for s in self._by_id.values()
            if document_type in s.document_types or "*" in s.document_types
        ]


def default_strategies() -> StrategyRegistry:
    """Return the registry of built-in discovery strategies."""

    reg = StrategyRegistry()
    reg.register(
        DiscoveryStrategy(
            strategy_id="synopsis_first_publication",
            version="1.0",
            document_types=("publication",),
            workflow_id="default_publication",
            synopsis_budget_chars=3000,
            chunk_budget=64,
            multimodal_enabled=True,
            description="Synopsis-first deepening for structured publications.",
        )
    )
    reg.register(
        DiscoveryStrategy(
            strategy_id="all_unit_sweep",
            version="1.0",
            document_types=("html_note", "markdown", "mixed", "unknown"),
            workflow_id="default_publication",
            chunk_budget=0,
            note_dump_enabled=True,
            description="Full unit sweep with note dumping for weakly structured corpora.",
        )
    )
    reg.register(
        DiscoveryStrategy(
            strategy_id="multimodal_first_slides",
            version="1.0",
            document_types=("slide_deck",),
            workflow_id="default_publication",
            multimodal_enabled=True,
            image_budget=64,
            description="Multimodal-first extraction for slide decks.",
        )
    )
    return reg
