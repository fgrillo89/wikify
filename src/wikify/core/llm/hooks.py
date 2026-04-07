"""LLM lifecycle hooks for cost tracking, budgets, and logging.

Hooks are injected into ``complete_structured()`` and
``validate_and_retry_text()`` via the ``hooks`` parameter. Each hook
receives an ``LLMEvent`` before and after every LLM call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class LLMEvent:
    """Data passed through the hook pipeline."""

    messages: list[dict[str, str]]
    model: str
    temperature: float
    max_tokens: int
    raw_response: str | None = None
    parsed_output: object | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    attempt: int = 0
    cached: bool = False


@runtime_checkable
class LLMHook(Protocol):
    """Protocol for LLM lifecycle hooks."""

    def before_call(self, event: LLMEvent) -> LLMEvent: ...
    def after_call(self, event: LLMEvent) -> LLMEvent: ...


# ── Concrete hooks ───────────────────────────────────────────────────────────


class CostTracker:
    """Accumulate estimated USD cost across all LLM calls in a run."""

    # Approximate per-token pricing (input_usd, output_usd)
    PRICING: dict[str, tuple[float, float]] = {
        "claude-sonnet-4-20250514": (3.0e-6, 15.0e-6),
        "claude-haiku-3.5": (0.25e-6, 1.25e-6),
    }

    def __init__(self) -> None:
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.calls: list[dict] = []

    def before_call(self, event: LLMEvent) -> LLMEvent:
        return event

    def after_call(self, event: LLMEvent) -> LLMEvent:
        self.total_input_tokens += event.input_tokens
        self.total_output_tokens += event.output_tokens
        in_price, out_price = self.PRICING.get(event.model, (3.0e-6, 15.0e-6))
        call_cost = event.input_tokens * in_price + event.output_tokens * out_price
        self.total_cost_usd += call_cost
        self.calls.append(
            {
                "model": event.model,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cost_usd": call_cost,
                "cached": event.cached,
            }
        )
        return event

    def summary(self) -> str:
        return (
            f"LLM calls: {len(self.calls)}, "
            f"tokens: {self.total_input_tokens}+{self.total_output_tokens}, "
            f"est. cost: ${self.total_cost_usd:.4f}"
        )


class TokenBudget:
    """Enforce a hard cap on total tokens consumed per run."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.spent: int = 0

    def before_call(self, event: LLMEvent) -> LLMEvent:
        if self.spent >= self.budget:
            raise RuntimeError(f"Token budget exhausted: {self.spent}/{self.budget}")
        return event

    def after_call(self, event: LLMEvent) -> LLMEvent:
        self.spent += event.input_tokens + event.output_tokens
        return event


class CallLogger:
    """Log every LLM call at DEBUG level."""

    def before_call(self, event: LLMEvent) -> LLMEvent:
        logger.debug(
            "LLM call [attempt=%d] model=%s temp=%.2f max_tokens=%d",
            event.attempt,
            event.model,
            event.temperature,
            event.max_tokens,
        )
        return event

    def after_call(self, event: LLMEvent) -> LLMEvent:
        logger.debug(
            "LLM response: %d chars, ~%d+%d tokens, %.0fms, cached=%s",
            len(event.raw_response or ""),
            event.input_tokens,
            event.output_tokens,
            event.latency_ms,
            event.cached,
        )
        return event
