"""Abstract base class for retrieval strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from wikify.core.config import settings

if TYPE_CHECKING:
    from wikify.core.graph.metrics import GraphMetrics
    from wikify.core.retrieve.context import RetrievedContext
    from wikify.core.store.models import PaperPlan


@dataclass
class StrategyConfig:
    """Common configuration for all retrieval strategies."""

    token_budget: int = 12_000
    deep_read_limit: int = 3
    shallow_chunk_count: int = 3
    max_traversal_depth: int = 2
    parallel_workers: int = 3
    model_for_synthesis: str = field(default_factory=lambda: settings.llm_fast_model)
    user_focus: str = ""  # Optional focus hint from the user prompt


class RetrievalStrategy(ABC):
    """Abstract base for retrieval strategies.

    Each strategy implements `retrieve()` which returns a `RetrievedContext`
    ready for the planner and writer to consume.
    """

    name: str = "base"
    expensive: bool = False  # True if the strategy uses LLM calls
    description: str = ""

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    @abstractmethod
    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,
    ) -> RetrievedContext:
        """Retrieve and assemble context for generation.

        Args:
            graph_metrics: Pre-computed graph metrics (computed if None).
            plan: Optional paper plan (needed by per-section strategies).
        """
        ...

    def estimate_cost(self) -> dict[str, float]:
        """Return estimated cost: {"llm_calls": N, "est_usd": X}."""
        return {"llm_calls": 0, "est_usd": 0.0}
