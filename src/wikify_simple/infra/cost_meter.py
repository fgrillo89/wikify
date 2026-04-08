"""Per-call accounting + budget gate + per-call event log.

One CostMeter per run. Threaded through every model call. Records the
input/output tokens, the tier, the wall time, the cache-hit flag, and the
context headroom for each call. Aggregates into a per-run snapshot and
appends one line to a JSONL event log.

Three guarantees:
  1. Hard abort on budget overrun at 1.05 x budget_target_haiku_eq.
  2. Hard abort on context overrun if a call's input_tokens exceed the
     declared context_cap (this should be impossible because the
     ContextEnvelope enforces the cap, but the assertion catches builder
     bugs).
  3. No silent zero-token calls. record() raises if input_tokens is None.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .role import Role

# --- pricing model -------------------------------------------------------
#
# Cost is token-based. Haiku is normalised to (input=1.0, output=1.0) per
# token in haiku-equivalent units. Larger tiers multiply those rates. Each
# tier also pays a fixed per-call overhead that captures skill-dispatch
# latency (prompt boilerplate, subagent spin-up, tool round-trips) that
# does not vanish just because a prompt is short.
#
# Constants (haiku-equivalent):
#   - per-token rates:
#       S:  input=1.0   output=1.0     (baseline haiku-like)
#       M:  input=12.0  output=15.0    (sonnet-like)
#       L:  input=60.0  output=75.0    (opus-like)
#   - per-call overhead:
#       S:   50 heq
#       M:  200 heq
#       L:  500 heq
#
# These numbers are deliberate ballparks: they preserve the pre-existing
# rankings (L >> M >> S) while ensuring that payload size -- including
# writer figures and extractor images -- drives the cost a call accrues.


@dataclass(frozen=True)
class TierPrice:
    """Per-token price + per-call overhead in haiku-equivalent units."""

    name: str
    input_per_m: float
    output_per_m: float
    fixed_overhead: float = 0.0

    def haiku_eq(self, tokens_in: int, tokens_out: int) -> float:
        token_cost = (
            tokens_in * self.input_per_m / 1_000_000.0
            + tokens_out * self.output_per_m / 1_000_000.0
        ) * 1_000_000.0  # back to "haiku-equivalent tokens"
        return token_cost + self.fixed_overhead


_DEFAULT_TIERS: dict[str, TierPrice] = {
    "S": TierPrice(name="S", input_per_m=1.0, output_per_m=1.0, fixed_overhead=50.0),
    "M": TierPrice(name="M", input_per_m=12.0, output_per_m=15.0, fixed_overhead=200.0),
    "L": TierPrice(name="L", input_per_m=60.0, output_per_m=75.0, fixed_overhead=500.0),
}


# --- per-call record -----------------------------------------------------


@dataclass(frozen=True)
class CallRecord:
    role: Role
    tier: str
    input_tokens: int
    output_tokens: int
    context_used: int
    context_cap: int
    wall_seconds: float
    cache_hit: bool
    prompt_hash: str
    haiku_eq: float

    def to_json(self) -> str:
        d = asdict(self)
        d["role"] = self.role.value
        return json.dumps(d, separators=(",", ":"))


# --- the meter -----------------------------------------------------------


@dataclass
class _Aggregates:
    calls: int = 0
    haiku_eq: float = 0.0
    wall_seconds: float = 0.0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    context_used_max: int = 0
    context_used_sum: int = 0
    headroom_min: int = 1 << 30
    headroom_sum: int = 0

    def update(self, r: CallRecord) -> None:
        self.calls += 1
        self.haiku_eq += r.haiku_eq
        self.wall_seconds += r.wall_seconds
        if r.cache_hit:
            self.cache_hits += 1
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.context_used_max = max(self.context_used_max, r.context_used)
        self.context_used_sum += r.context_used
        headroom = r.context_cap - r.context_used
        self.headroom_min = min(self.headroom_min, headroom)
        self.headroom_sum += headroom

    def to_dict(self) -> dict:
        if self.calls == 0:
            return {"calls": 0}
        return {
            "calls": self.calls,
            "haiku_eq": self.haiku_eq,
            "wall_seconds": self.wall_seconds,
            "cache_hit_rate": self.cache_hits / self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "context_used_max": self.context_used_max,
            "context_used_mean": self.context_used_sum / self.calls,
            "headroom_min": self.headroom_min,
            "headroom_mean": self.headroom_sum / self.calls,
        }


class BudgetExceeded(RuntimeError):
    """Cost budget breached; the run aborts cleanly."""


class CostMeter:
    """Per-run cost accounting + budget gate + event log.

    Construct one per run. Pass the same instance to every binding call.
    """

    _ABORT_RATIO = 1.05
    _STATUS_EVERY_CALLS = 10
    _STATUS_EVERY_SECONDS = 5.0

    def __init__(
        self,
        budget_haiku_eq: float,
        run_id: str,
        events_path: Path,
        tiers: dict[str, TierPrice] | None = None,
        status_stream=sys.stderr,
    ) -> None:
        self._budget = budget_haiku_eq
        self._run_id = run_id
        self._events_path = events_path
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        self._tiers = tiers or dict(_DEFAULT_TIERS)
        self._status_stream = status_stream
        self._total = _Aggregates()
        self._by_role: dict[Role, _Aggregates] = {r: _Aggregates() for r in Role}
        self._by_tier: dict[str, _Aggregates] = {}
        self._last_status_call = 0
        self._last_status_t = time.monotonic()

    @property
    def budget(self) -> float:
        return self._budget

    @property
    def spent_haiku_eq(self) -> float:
        return self._total.haiku_eq

    def haiku_eq_for(self, tier: str, tokens_in: int, tokens_out: int) -> float:
        return self._tiers[tier].haiku_eq(tokens_in, tokens_out)

    def record(
        self,
        role: Role,
        tier: str,
        input_tokens: int | None,
        output_tokens: int | None,
        context_cap: int,
        wall_seconds: float,
        cache_hit: bool,
        prompt_hash: str,
    ) -> CallRecord:
        if input_tokens is None or output_tokens is None:
            raise RuntimeError("CostMeter.record refuses None token counts")
        if input_tokens > context_cap:
            raise RuntimeError(f"context overrun: {input_tokens} > {context_cap} ({role}/{tier})")
        haiku_eq = self.haiku_eq_for(tier, input_tokens, output_tokens)
        record = CallRecord(
            role=role,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_used=input_tokens,
            context_cap=context_cap,
            wall_seconds=wall_seconds,
            cache_hit=cache_hit,
            prompt_hash=prompt_hash,
            haiku_eq=haiku_eq,
        )
        self._total.update(record)
        self._by_role[role].update(record)
        self._by_tier.setdefault(tier, _Aggregates()).update(record)
        with self._events_path.open("a", encoding="utf-8") as f:
            f.write(record.to_json() + "\n")
        self._maybe_print_status()
        if self._total.haiku_eq > self._budget * self._ABORT_RATIO:
            raise BudgetExceeded(f"spent {self._total.haiku_eq:.0f} > 1.05 x {self._budget:.0f}")
        return record

    def snapshot(self) -> dict:
        return {
            "run_id": self._run_id,
            "budget_target_haiku_eq": self._budget,
            "budget_used_haiku_eq": self._total.haiku_eq,
            "wall_seconds": self._total.wall_seconds,
            "by_role": {r.value: agg.to_dict() for r, agg in self._by_role.items()},
            "by_tier": {t: agg.to_dict() for t, agg in self._by_tier.items()},
            "context": {
                "used_max": self._total.context_used_max,
                "used_mean": (
                    self._total.context_used_sum / self._total.calls if self._total.calls else 0
                ),
                "headroom_min": (self._total.headroom_min if self._total.calls else 0),
                "headroom_mean": (
                    self._total.headroom_sum / self._total.calls if self._total.calls else 0
                ),
            },
            "calls": self._total.calls,
            "cache_hit_rate": (
                self._total.cache_hits / self._total.calls if self._total.calls else 0.0
            ),
        }

    def write_snapshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")

    def _maybe_print_status(self) -> None:
        now = time.monotonic()
        calls_since = self._total.calls - self._last_status_call
        time_since = now - self._last_status_t
        if calls_since < self._STATUS_EVERY_CALLS and time_since < self._STATUS_EVERY_SECONDS:
            return
        self._last_status_call = self._total.calls
        self._last_status_t = now
        pct = 100.0 * self._total.haiku_eq / self._budget if self._budget else 0.0
        line = (
            f"[{self._run_id}] calls={self._total.calls} "
            f"spent={self._total.haiku_eq:.0f}heq ({pct:.1f}%) "
            f"hits={self._total.cache_hits} "
            f"max_ctx={self._total.context_used_max}"
        )
        print(line, file=self._status_stream, flush=True)
