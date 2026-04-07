"""Strategy planner: choose a ``DiscoveryStrategy`` for a ``DocumentProfile``.

Routing is intentionally explicit and document-type aware. The default
behavior is "first matching strategy by document type", but callers may
pass an override map for experimental routing.
"""

from __future__ import annotations

from wikify.wiki.discovery.contracts import DiscoveryStrategy, DocumentProfile
from wikify.wiki.discovery.strategies import StrategyRegistry


class DiscoveryPlanner:
    def __init__(
        self,
        strategies: StrategyRegistry,
        *,
        overrides: dict[str, str] | None = None,
        fallback_strategy_id: str = "all_unit_sweep",
    ) -> None:
        self.strategies = strategies
        self.overrides = overrides or {}
        self.fallback_strategy_id = fallback_strategy_id

    def choose(self, profile: DocumentProfile) -> DiscoveryStrategy:
        if profile.document_id in self.overrides:
            return self.strategies.get(self.overrides[profile.document_id])
        candidates = self.strategies.for_document_type(profile.document_type)
        if candidates:
            return candidates[0]
        return self.strategies.get(self.fallback_strategy_id)
