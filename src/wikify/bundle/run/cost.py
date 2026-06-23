"""Cost aggregation from ``run/events.jsonl``.

Per-call cost is recorded as a ``type="call"`` event in
``events.jsonl``; this module aggregates those events into per-stage
and per-model totals. Cost is in haiku-equivalent units; the
:class:`TierPrice` table maps tiers to their relative price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ...api import Bundle
from ...config import (
    TIER_L_INPUT,
    TIER_L_OUTPUT,
    TIER_L_OVERHEAD,
    TIER_M_INPUT,
    TIER_M_OUTPUT,
    TIER_M_OVERHEAD,
    TIER_S_INPUT,
    TIER_S_OUTPUT,
    TIER_S_OVERHEAD,
)
from ...types import ModelTier
from .events import Event, iter_events


@dataclass(frozen=True)
class TierPrice:
    """Per-token price + per-call overhead in haiku-equivalent units."""

    tier: ModelTier
    input_per_m: float
    output_per_m: float
    fixed_overhead: float = 0.0

    def haiku_eq(self, tokens_in: int, tokens_out: int) -> float:
        token_cost = (
            tokens_in * self.input_per_m + tokens_out * self.output_per_m
        ) / 1_000_000.0 * 1_000_000.0
        return token_cost + self.fixed_overhead


_DEFAULT_TIERS: dict[ModelTier, TierPrice] = {
    ModelTier.SMALL: TierPrice(
        tier=ModelTier.SMALL,
        input_per_m=TIER_S_INPUT,
        output_per_m=TIER_S_OUTPUT,
        fixed_overhead=TIER_S_OVERHEAD,
    ),
    ModelTier.MEDIUM: TierPrice(
        tier=ModelTier.MEDIUM,
        input_per_m=TIER_M_INPUT,
        output_per_m=TIER_M_OUTPUT,
        fixed_overhead=TIER_M_OVERHEAD,
    ),
    ModelTier.LARGE: TierPrice(
        tier=ModelTier.LARGE,
        input_per_m=TIER_L_INPUT,
        output_per_m=TIER_L_OUTPUT,
        fixed_overhead=TIER_L_OVERHEAD,
    ),
}


def haiku_eq_for(tier: ModelTier | str, tokens_in: int, tokens_out: int) -> float:
    """Return the haiku-equivalent cost for a single call at ``tier``."""
    t = tier if isinstance(tier, ModelTier) else ModelTier(tier)
    return _DEFAULT_TIERS[t].haiku_eq(tokens_in, tokens_out)


def _initial_agg() -> dict[str, dict[str, float]]:
    return {
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "haiku_eq": 0.0,
            "calls": 0,
            "wall_seconds": 0.0,
            "cache_hits": 0,
        },
        "by_tier": {},
        "by_role": {},
    }


def _bump(agg: dict[str, float], record: dict) -> None:
    agg["input_tokens"] += int(record.get("input_tokens", 0) or 0)
    agg["output_tokens"] += int(record.get("output_tokens", 0) or 0)
    agg["haiku_eq"] += float(record.get("haiku_eq", 0.0) or 0.0)
    agg["calls"] += 1
    agg["wall_seconds"] += float(record.get("wall_seconds", 0.0) or 0.0)
    if record.get("cache_hit"):
        agg["cache_hits"] += 1


def aggregate(events: Iterable[Event]) -> dict:
    """Roll up every ``type="call"`` event in ``events`` into totals + breakdowns.

    Returns a dict shaped::

        {
          "totals": {input_tokens, output_tokens, haiku_eq, calls, wall_seconds, cache_hits},
          "by_tier": {<tier>: {...same shape...}, ...},
          "by_role": {<role>: {...same shape...}, ...},
        }
    """
    agg = _initial_agg()
    for ev in events:
        if ev.type != "call":
            continue
        data = ev.data or {}
        _bump(agg["totals"], data)
        tier = str(data.get("tier", "?"))
        if tier not in agg["by_tier"]:
            agg["by_tier"][tier] = _initial_agg()["totals"]
        _bump(agg["by_tier"][tier], data)
        role = str(data.get("role", "?"))
        if role not in agg["by_role"]:
            agg["by_role"][role] = _initial_agg()["totals"]
        _bump(agg["by_role"][role], data)
    return agg


def cost_summary(bundle: Bundle) -> dict:
    """Read the bundle's events.jsonl and return the cost aggregate."""
    return aggregate(iter_events(bundle))


def reconcile_spent(bundle: Bundle) -> int:
    """Persist ``budget.spent_haiku_eq`` from the call-event aggregate.

    The call events are the single source of truth for spend; ``spent_haiku_eq``
    is a cache of their total. Reconciling on every recorded call keeps the
    STOP-CHECK budget bound — which reads the stored field — faithful instead of
    stuck at its initial value. Returns the reconciled total.
    """
    from .state import load_state, save_state

    total = int(round(aggregate(iter_events(bundle))["totals"]["haiku_eq"]))
    state = load_state(bundle)
    if state.budget.spent_haiku_eq != total:
        state.budget = state.budget.model_copy(update={"spent_haiku_eq": total})
        save_state(bundle, state)
    return total
