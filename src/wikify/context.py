"""Context envelope builder, role specs, and token counting."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Union

from .config import CHARS_PER_TOKEN
from .types import Role

# --- token counting ------------------------------------------------------


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in `text`.

    Returns 0 for empty input. The estimate is a rule of thumb; callers
    that need exact counts should not use this function.
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


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


# --- global caps ---------------------------------------------------------

_TOTAL_CONTEXT = 128_000  # tokens; effective input cap = TOTAL - RESERVE
_RESPONSE_RESERVE = 8_000


def total_context() -> int:
    return _TOTAL_CONTEXT


def response_reserve() -> int:
    return _RESPONSE_RESERVE


# --- role specs ----------------------------------------------------------

# Pool names are stable identifiers; the strategy code populates pools by
# these names when constructing a request.

_EXTRACTOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Required(name="target_chunk", fixed_tokens=None),  # variable
    Pool(name="canonical_titles", floor_tokens=1_000, ceiling_tokens=4_000),
]

_WRITER_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Required(name="page_skeleton", fixed_tokens=None),  # variable
    Pool(name="evidence_chunks", floor_tokens=4_000, ceiling_tokens=80_000),
    Pool(name="neighbor_summaries", floor_tokens=0, ceiling_tokens=8_000),
]

_COMPACTOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=500),
    Pool(name="dossier_entries", floor_tokens=2_000, ceiling_tokens=20_000),
]

_EDITOR_SPEC: list[SlotSpec] = [
    Required(name="schema", fixed_tokens=1_000),
    Pool(name="dossier", floor_tokens=2_000, ceiling_tokens=30_000),
    Pool(name="wiki_index", floor_tokens=1_000, ceiling_tokens=10_000),
]

_ORCHESTRATOR_SPEC: list[SlotSpec] = [
    Required(name="state_header", fixed_tokens=2_000),
    Required(name="action_menu", fixed_tokens=2_000),
    Pool(name="page_index", floor_tokens=4_000, ceiling_tokens=40_000),
    Pool(name="action_history", floor_tokens=4_000, ceiling_tokens=20_000),
    Pool(name="open_candidates", floor_tokens=2_000, ceiling_tokens=20_000),
]


_SPECS: dict[Role, list[SlotSpec]] = {
    Role.EXTRACTOR: _EXTRACTOR_SPEC,
    Role.COMPACTOR: _COMPACTOR_SPEC,
    Role.EDITOR: _EDITOR_SPEC,
    Role.WRITER: _WRITER_SPEC,
    Role.ORCHESTRATOR: _ORCHESTRATOR_SPEC,
}


def role_spec(role: Role) -> list[SlotSpec]:
    return _SPECS[role]
