"""Strategy configuration, budget allocation, and run modes.

A distill run's behavior is defined by:
- Explorer: which chunks to process (see explorer.py)
- Budget: how to split resources between extract and write
- Tiers: which model size per role (S/M/L)
- Mode: scripted (algorithmic) vs guided (orchestrator-driven)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ..config import CURATE_FRACTION, NOVELTY_THRESHOLD
from ..schema import OrchState
from ..types import ModelTier, Orchestrator, StrategyId
from .explorer import (
    Explorer,
    ExplorerState,
    ExtractDecision,
    GlobalOp,
    LevyExplorer,
    LocalOp,
    build_snapshot,
    execute_action,
)

if TYPE_CHECKING:
    from ..models import WikiPage


# ---- Section 1: Budget allocation (was schedule.py) ----------------------


@dataclass(frozen=True)
class BudgetSplit:
    extract_haiku_eq: float
    write_haiku_eq: float
    curate_haiku_eq: float


class BudgetAllocator(Protocol):
    def initial_split(self, total: float) -> BudgetSplit: ...
    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit: ...


@dataclass(frozen=True)
class StaticBudget(BudgetAllocator):
    exploit_fraction: float

    def initial_split(self, total: float) -> BudgetSplit:
        curate = CURATE_FRACTION * total
        exploit = self.exploit_fraction * total
        explore = max(total - curate - exploit, 0.0)
        return BudgetSplit(extract_haiku_eq=explore, write_haiku_eq=exploit, curate_haiku_eq=curate)

    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit:
        return self.initial_split(remaining)


@dataclass(frozen=True)
class AdaptiveBudget(BudgetAllocator):
    exploit_fraction_initial: float
    novelty_threshold: float = NOVELTY_THRESHOLD

    def initial_split(self, total: float) -> BudgetSplit:
        return StaticBudget(self.exploit_fraction_initial).initial_split(total)

    def reallocate(self, remaining: float, novelty_rate: float) -> BudgetSplit:
        ef = self.exploit_fraction_initial
        if novelty_rate < self.novelty_threshold:
            ef = max(ef, 0.7)
        return StaticBudget(ef).initial_split(remaining)


# ---- Section 2: Strategy config + table + factory (was registry.py) ------


@dataclass
class StrategyConfig:
    name: str
    explorer: Explorer
    budget: BudgetAllocator
    extract_tier: ModelTier
    write_tier: ModelTier
    edit_tier: ModelTier = ModelTier.MEDIUM
    compact_tier: ModelTier = ModelTier.SMALL
    orchestrate_tier: ModelTier = ModelTier.LARGE
    # Allocation override. When not None, replaces the budget's
    # exploit_fraction for the initial split. The guided mode can still
    # mutate the allocation mid-run via set_allocation actions.
    exploit_fraction_override: float | None = None
    seed: int = 0

    def __post_init__(self) -> None:
        self.extract_tier = ModelTier(self.extract_tier)
        self.write_tier = ModelTier(self.write_tier)
        self.edit_tier = ModelTier(self.edit_tier)
        self.compact_tier = ModelTier(self.compact_tier)
        self.orchestrate_tier = ModelTier(self.orchestrate_tier)


STRATEGY_CONFIGS: dict[str, dict[str, Any]] = {
    StrategyId.EXPLORE.value: dict(
        name="E",
        explorer=LevyExplorer(
            local_op=LocalOp.NONE,
            global_op=GlobalOp.PAGERANK,
            jump_rate=1.0,
        ),
        budget=StaticBudget(exploit_fraction=0.2),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.SMALL,
    ),
    StrategyId.MIXED.value: dict(
        name="M",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        budget=AdaptiveBudget(exploit_fraction_initial=0.65),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.MEDIUM,
    ),
    StrategyId.EXPLOIT.value: dict(
        name="X",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.UNIFORM,  # never used: jump_rate=0
            jump_rate=0.0,
        ),
        budget=StaticBudget(exploit_fraction=0.6),
        extract_tier=ModelTier.MEDIUM,
        write_tier=ModelTier.MEDIUM,
    ),
}


def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return StrategyConfig(**STRATEGY_CONFIGS[key], seed=seed)


# ---- Section 3: Run mode (was policy.py) ---------------------------------

ModeName = Literal["scripted", "guided"]


@dataclass
class RuntimeOverrides:
    """Mutable view into tier + allocation settings for the guided mode.

    The pipeline creates one instance at startup, populates it from the
    strategy config, and passes it to ``build_mode``. The guided mode
    mutates fields in response to orchestrator actions. The pipeline
    reads the fields on every extract / write iteration.

    Today only ``extract_tier`` and ``write_tier`` are actually plumbed
    into the per-call dispatch. ``edit_tier`` and ``compact_tier`` are
    tracked here for symmetry and telemetry, but the Compactor/Editor
    protocols do not currently expose a tier argument, so set_tier on
    those roles updates the runtime state without affecting the
    bindings' own (hardcoded) tier selection. This is intentional: the
    editor is reached via set_tier and it's the orchestrator's own
    call, not a dispatch argument.
    """

    extract_tier: ModelTier = ModelTier.SMALL
    write_tier: ModelTier = ModelTier.MEDIUM
    edit_tier: ModelTier = ModelTier.MEDIUM
    compact_tier: ModelTier = ModelTier.SMALL
    # orchestrate_tier is locked at "L"; the guided mode cannot change it
    orchestrate_tier: ModelTier = ModelTier.LARGE
    # exploit_fraction in [0, 1]. None means "use budget default".
    exploit_fraction: float | None = None
    # Reallocation epoch: incremented whenever the guided mode sets a
    # new allocation, so the pipeline knows to re-split the remaining
    # budget on the next iteration.
    allocation_epoch: int = 0


@dataclass(frozen=True)
class ModeContext:
    run_id: str
    n_pages: int
    n_candidates: int
    n_concepts: int
    n_people: int
    docs_covered: int
    docs_total: int


class RunMode(Protocol):
    def next_extract(self, state: ExplorerState, k: int, ctx: ModeContext) -> ExtractDecision: ...
    def order_write_pages(
        self, pages: list[WikiPage], max_concepts: int, ctx: ModeContext
    ) -> list[WikiPage]: ...
    def drain_events(self) -> list[dict]: ...


class ScriptedMode:
    """Deterministic mode: explore with the configured explorer."""

    def __init__(self, explorer: Explorer) -> None:
        self._explorer = explorer
        self._events: list[dict] = []

    def next_extract(self, state: ExplorerState, k: int, ctx: ModeContext) -> ExtractDecision:
        batch = self._explorer.next_batch(state, k)
        decision = ExtractDecision(action="sample_batch", batch=tuple(batch), stop=not bool(batch))
        self._events.append(
            {
                "stage": "extract",
                "mode": "scripted",
                "action": decision.action,
                "n_chunks": len(batch),
                "stop": decision.stop,
                "n_pages": ctx.n_pages,
                "n_candidates": ctx.n_candidates,
            }
        )
        return decision

    def order_write_pages(
        self, pages: list[WikiPage], max_concepts: int, ctx: ModeContext
    ) -> list[WikiPage]:
        ordered = pages[:max_concepts]
        self._events.append(
            {
                "stage": "write",
                "mode": "scripted",
                "action": "sequential",
                "n_pages": len(ordered),
                "docs_covered": ctx.docs_covered,
            }
        )
        return ordered

    def drain_events(self) -> list[dict]:
        out = list(self._events)
        self._events.clear()
        return out


class GuidedMode:
    """Orchestrator-driven mode with deterministic explorer execution.

    The orchestrator chooses an action; this class executes it against the
    same explorer state used by scripted strategies so telemetry is comparable.
    Control actions (``set_allocation``, ``set_tier``) mutate the shared
    ``RuntimeOverrides`` so subsequent pipeline iterations pick up the change.

    Cost note: the orchestrator runs at tier L (opus) and a single
    decision costs ~30k haiku-equivalent tokens. Calling it on every
    extract batch would exhaust the budget on orchestration alone.
    Instead, an active exploration action (``walk_local``, ``jump_*``) is
    cached and re-used for up to ``persist_batches`` subsequent batches
    before re-querying the orchestrator. Control actions
    (``set_tier``, ``set_allocation``) and ``done`` are never cached.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        fallback_explorer: Explorer,
        runtime: RuntimeOverrides | None = None,
        persist_batches: int = 8,
    ) -> None:
        self._orchestrator = orchestrator
        self._fallback_explorer = fallback_explorer
        self._runtime = runtime or RuntimeOverrides()
        self._last_actions: list[str] = []
        self._events: list[dict] = []
        # Persist the last active action (jump_*, walk_local) for this
        # many consecutive batches before re-querying the orchestrator.
        self._persist_batches = max(1, persist_batches)
        self._cached_action_name: str | None = None
        self._cached_action_args: dict = {}
        self._batches_remaining: int = 0

    @property
    def runtime(self) -> RuntimeOverrides:
        return self._runtime

    def next_extract(self, state: ExplorerState, k: int, ctx: ModeContext) -> ExtractDecision:
        # Reuse the cached active action if it still has batches remaining.
        # This avoids a tier-L orchestrator dispatch on every single batch.
        if self._batches_remaining > 0 and self._cached_action_name is not None:
            self._batches_remaining -= 1
            action_name = self._cached_action_name
            action_args = dict(self._cached_action_args)
            cached = True
        else:
            orch_state = OrchState(
                run_id=ctx.run_id,
                n_pages=ctx.n_pages,
                n_candidates=ctx.n_candidates,
                n_concepts=ctx.n_concepts,
                n_people=ctx.n_people,
                docs_covered=ctx.docs_covered,
                docs_total=ctx.docs_total,
                last_actions=self._last_actions[-16:],
                sampler_snapshot=build_snapshot(state),
            )
            action = self._orchestrator.step(orch_state)
            action_name = action.name
            action_args = dict(action.args or {})
            # Cache active exploration actions so we don't pay a tier-L call
            # per batch. Control actions (set_*, done) and pick_chunks are
            # never cached (pick_chunks is already targeted; no reason to repeat).
            _cacheable = ("walk_local", "jump_uniform", "jump_pagerank", "jump_gap", "jump_figures")
            if action_name in _cacheable:
                self._cached_action_name = action_name
                self._cached_action_args = action_args
                self._batches_remaining = self._persist_batches - 1
            else:
                self._cached_action_name = None
                self._cached_action_args = {}
                self._batches_remaining = 0
            cached = False
        self._last_actions.append(action_name)
        decision = execute_action(
            action_name, action_args, state, k,
            self._fallback_explorer, self._runtime,
        )
        event: dict = {
            "stage": "extract",
            "mode": "guided",
            "action": action_name,
            "n_chunks": len(decision.batch),
            "stop": decision.stop,
            "args": action_args,
            "cached": cached,
        }
        if action_name == "pick_chunks":
            event["reason"] = action_args.get("reason", "")
        self._events.append(event)
        return decision

    def order_write_pages(
        self, pages: list[WikiPage], max_concepts: int, ctx: ModeContext
    ) -> list[WikiPage]:
        # v1: guided mode controls exploration; write ordering stays deterministic.
        ordered = pages[:max_concepts]
        self._events.append(
            {
                "stage": "write",
                "mode": "guided",
                "action": "sequential",
                "n_pages": len(ordered),
                "docs_covered": ctx.docs_covered,
            }
        )
        return ordered

    def drain_events(self) -> list[dict]:
        out = list(self._events)
        self._events.clear()
        return out


def build_mode(
    *,
    name: ModeName,
    explorer: Explorer,
    orchestrator: Orchestrator | None,
    runtime: RuntimeOverrides | None = None,
) -> RunMode:
    match name:
        case "scripted":
            return ScriptedMode(explorer)
        case "guided":
            if orchestrator is None:
                raise ValueError("guided mode requires an orchestrator binding")
            return GuidedMode(orchestrator, explorer, runtime=runtime)
        case _:
            raise ValueError(f"unknown mode: {name}")
