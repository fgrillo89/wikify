"""Priority-fill context envelope builder.

Builds a single prompt string for a model call, guaranteed to fit within
the global context cap. The cap is enforced here and only here. No model,
no agent, no skill is trusted to respect a context budget — the prompt
they receive is constructed by this builder and is the only thing they
ever see.

The builder walks a per-role spec list:
  1. Every Required slot gets its fixed share (or its variable content's
     actual length).
  2. Every Pool slot gets at least its floor.
  3. Leftover budget is distributed top-down by spec order until the
     effective cap is hit.
  4. Items in any pool that don't fit are summarised in one line
     ("23 more elided").

If the Required slots alone exceed the effective cap, the builder raises
ContextOverflowError rather than truncating silently.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Union

from .tokens import count_tokens

# --- slot specs ----------------------------------------------------------


@dataclass(frozen=True)
class Required:
    """A slot whose content is mandatory.

    `fixed_tokens` is the budget reservation for this slot. If None, the
    slot is variable-size and consumes whatever its actual content needs.
    """

    name: str
    fixed_tokens: int | None


@dataclass(frozen=True)
class Pool:
    """A slot filled from a list of items, ranked, until the budget runs out."""

    name: str
    floor_tokens: int
    ceiling_tokens: int


SlotSpec = Union[Required, Pool]


# --- exceptions ----------------------------------------------------------


class ContextOverflowError(RuntimeError):
    """Required slots alone exceed the effective context cap."""


class UnknownSlotError(KeyError):
    """A request supplied content for a slot the spec does not declare."""


# --- request shape -------------------------------------------------------


@dataclass(frozen=True)
class PoolItem:
    """One ranked item to be considered for inclusion in a Pool."""

    text: str
    rank: float  # higher = more important; the builder sorts descending


SlotContent = Union[str, list[PoolItem]]


# --- the builder ---------------------------------------------------------


class ContextEnvelope:
    """Builds prompts that fit a fixed context cap.

    Construct one per role, then call `build(slots)` for each model call.
    The same instance can be reused; it holds no per-call state.
    """

    def __init__(
        self,
        spec: Sequence[SlotSpec],
        total_context: int,
        response_reserve: int,
    ) -> None:
        self._spec = list(spec)
        self._total_context = total_context
        self._response_reserve = response_reserve
        self._effective_cap = total_context - response_reserve
        self._spec_by_name = {s.name: s for s in self._spec}

    @property
    def effective_cap(self) -> int:
        return self._effective_cap

    def build(self, slots: dict[str, SlotContent]) -> str:
        """Assemble a prompt from `slots`, guaranteed to fit `effective_cap`.

        `slots` is a dict keyed by slot name. Required slots must have a
        string value; Pool slots must have a list[PoolItem] value. Slots
        not declared in the spec raise UnknownSlotError.
        """
        for name in slots:
            if name not in self._spec_by_name:
                raise UnknownSlotError(name)

        # Pass 1: account for Required slots.
        required_text: dict[str, str] = {}
        required_tokens = 0
        for slot in self._spec:
            if not isinstance(slot, Required):
                continue
            content = slots.get(slot.name, "")
            if not isinstance(content, str):
                raise TypeError(f"Required slot {slot.name} expects str")
            tokens = slot.fixed_tokens if slot.fixed_tokens is not None else count_tokens(content)
            required_text[slot.name] = content
            required_tokens += tokens

        if required_tokens > self._effective_cap:
            raise ContextOverflowError(
                f"required slots use {required_tokens} > {self._effective_cap}"
            )

        # Pass 2: give every Pool its floor.
        remaining = self._effective_cap - required_tokens
        pool_budget: dict[str, int] = {}
        for slot in self._spec:
            if not isinstance(slot, Pool):
                continue
            floor = min(slot.floor_tokens, remaining)
            pool_budget[slot.name] = floor
            remaining -= floor

        # Pass 3: distribute leftover top-down by spec order, up to ceilings.
        for slot in self._spec:
            if not isinstance(slot, Pool) or remaining <= 0:
                continue
            headroom = slot.ceiling_tokens - pool_budget[slot.name]
            if headroom <= 0:
                continue
            grant = min(headroom, remaining)
            pool_budget[slot.name] += grant
            remaining -= grant

        # Pass 4: render each pool by greedy descending rank into its budget.
        pool_text: dict[str, str] = {}
        for slot in self._spec:
            if not isinstance(slot, Pool):
                continue
            items_raw = slots.get(slot.name, [])
            if not isinstance(items_raw, list):
                raise TypeError(f"Pool slot {slot.name} expects list[PoolItem]")
            items: list[PoolItem] = sorted(items_raw, key=lambda i: -i.rank)
            budget = pool_budget[slot.name]
            chosen: list[str] = []
            used = 0
            kept = 0
            for item in items:
                cost = count_tokens(item.text)
                if used + cost > budget:
                    continue
                chosen.append(item.text)
                used += cost
                kept += 1
            elided = len(items) - kept
            if elided > 0:
                chosen.append(f"[{elided} more elided]")
            pool_text[slot.name] = "\n".join(chosen)

        # Pass 5: emit in spec order.
        out_lines: list[str] = []
        for slot in self._spec:
            header = f"## {slot.name}"
            body = (
                required_text.get(slot.name, "")
                if isinstance(slot, Required)
                else pool_text.get(slot.name, "")
            )
            out_lines.append(header)
            if body:
                out_lines.append(body)
        return "\n\n".join(out_lines)
