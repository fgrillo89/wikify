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

GuidedTools = Literal["navigate", "full"]

# ``ModeName`` is defined early so PresetConfig can reference it. The
# build_mode dispatch only knows about ``scripted`` / ``guided``;
# ``baseline`` is a CLI-level routing flag that sends the run through
# ``baselines/pipeline.py`` instead of the LevyExplorer pipeline.
ModeName = Literal["scripted", "guided", "baseline"]

# Tool-set constants for guided mode (study-design.md §Tool filtering).
# Includes both the explorer action vocabulary (pick_chunks, walk_local,
# jump_*) used by the current single-turn orchestrator AND the KG tool
# names (search_chunks, get_citations, etc.) for the future multi-turn
# dispatch. Terminal actions: sample_chunks/pick_chunks, write_now.
NAVIGATE_TOOLS: frozenset[str] = frozenset({
    # Explorer actions (current orchestrator vocabulary)
    "walk_local", "jump_uniform", "jump_pagerank", "jump_gap",
    "jump_figures", "pick_chunks", "sample_chunks", "write_now",
    # KG tools (future multi-turn dispatch)
    "search_chunks", "get_source_info", "list_sources",
    "get_citations", "get_coverage", "get_pages", "get_budget",
})
FULL_TOOLS: frozenset[str] = NAVIGATE_TOOLS | {
    "done", "set_allocation", "set_tier",
}


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
    # ``balanced`` is the canonical scripted condition for the small-scale
    # study: mixed explorer (similarity_walk + coverage_gap, jump_rate=0.1)
    # with a fixed 60/35/5 extract/write/curate split and S/M/M/S tiers.
    # The fixed allocator makes cross-condition comparisons clean; guided
    # remains the only adaptive-allocation condition (via set_allocation).
    # Follow-on slots (``high-exploration``, ``high-exploitation``,
    # ``no-navigation``) will land here when they are implemented.
    StrategyId.BALANCED.value: dict(
        name="balanced",
        explorer=LevyExplorer(
            local_op=LocalOp.SIMILARITY_WALK,
            global_op=GlobalOp.COVERAGE_GAP,
            jump_rate=0.1,
        ),
        budget=StaticBudget(exploit_fraction=0.35),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.MEDIUM,
        edit_tier=ModelTier.MEDIUM,
        compact_tier=ModelTier.SMALL,
    ),
}


def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return StrategyConfig(**STRATEGY_CONFIGS[key], seed=seed)


@dataclass(frozen=True)
class PresetConfig:
    """Resolved preset: strategy + mode + tool filtering."""

    strategy: StrategyConfig
    mode: ModeName
    allowed_tools: frozenset[str] | None  # None for scripted modes


# Canonical study surface for the small-scale run (see
# docs/distill-test-readiness.md). The main comparison table is exactly
# ``baseline`` / ``balanced`` / ``guided``; everything else is either a
# follow-on slot or a legacy alias kept only for migration.
#
# - ``baseline``: abstract-first, source-grounded retrieve-and-synthesise.
#   No active navigation loop, no graph walk after each read; same default
#   tiers and 60/35/5 split as ``balanced``. Resolved by the CLI to the
#   baseline pipeline (``baselines/pipeline.py``), not the LevyExplorer
#   loop. ``strategy_id`` here is set to ``balanced`` only so callers that
#   inspect the strategy config (e.g., for tier defaults) see a coherent
#   view; the explorer is never invoked.
# - ``balanced``: scripted mixed explorer with fixed 60/35/5 split.
# - ``guided``: guided mode over the same base as ``balanced``, full tools
#   (set_allocation, set_tier, done, write_now, ...). The only adaptive-
#   allocation condition; ``write_now`` may dip into the reserved write
#   headroom — accepted as part of the guided treatment.
PRESET_CONFIGS: dict[str, dict[str, Any]] = {
    "baseline": dict(strategy_id="balanced", mode="baseline", guided_tools=None),
    "balanced": dict(strategy_id="balanced", mode="scripted", guided_tools=None),
    "guided": dict(strategy_id="balanced", mode="guided", guided_tools="full"),
}

# Names that the CLI / study driver should advertise as canonical. Other
# entries in PRESET_CONFIGS resolve, but are not surfaced as primary
# choices for the small-scale run.
CANONICAL_PRESETS: tuple[str, ...] = ("baseline", "balanced", "guided")


def build_preset(preset_id: str, *, seed: int = 0) -> PresetConfig:
    """Resolve a named preset to (strategy, mode, allowed_tools)."""
    cfg = PRESET_CONFIGS[preset_id]
    strategy = build_strategy(cfg["strategy_id"], seed=seed)
    gt = cfg["guided_tools"]
    allowed = NAVIGATE_TOOLS if gt == "navigate" else FULL_TOOLS if gt == "full" else None
    return PresetConfig(
        strategy=strategy,
        mode=cfg["mode"],
        allowed_tools=allowed,
    )


# ---- Section 3: Run mode (was policy.py) ---------------------------------


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
    budget_spent: float = 0.0
    budget_remaining: float = 0.0


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
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._fallback_explorer = fallback_explorer
        self._runtime = runtime or RuntimeOverrides()
        self._allowed_tools = allowed_tools
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
                sampler_snapshot=build_snapshot(
                    state,
                    budget_spent=ctx.budget_spent,
                    budget_remaining=ctx.budget_remaining,
                ),
                budget_spent=ctx.budget_spent,
                budget_remaining=ctx.budget_remaining,
            )
            action = self._orchestrator.step(orch_state)
            action_name = action.name
            action_args = dict(action.args or {})
            # Filter: reject actions outside the allowed tool set.
            if self._allowed_tools is not None and action_name not in self._allowed_tools:
                batch = self._fallback_explorer.next_batch(state, k)
                return ExtractDecision(
                    action="fallback_filtered",
                    batch=tuple(batch),
                    stop=not bool(batch),
                    meta={"blocked_action": action_name},
                )
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
    allowed_tools: frozenset[str] | None = None,
) -> RunMode:
    match name:
        case "scripted":
            return ScriptedMode(explorer)
        case "guided":
            if orchestrator is None:
                raise ValueError("guided mode requires an orchestrator binding")
            return GuidedMode(orchestrator, explorer, runtime=runtime, allowed_tools=allowed_tools)
        case _:
            raise ValueError(f"unknown mode: {name}")
