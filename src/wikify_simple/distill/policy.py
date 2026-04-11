"""Policy layer for extraction/write action selection.

Both deterministic strategies (E/M/X) and orchestrator-driven runs expose the
same action telemetry surface so strategy comparisons are directly comparable.

The ``LlmPolicy`` accepts an optional ``PolicyRuntime`` it can mutate in
response to orchestrator ``set_allocation`` / ``set_tier`` actions. The
pipeline reads the runtime's fields (tier, allocation) between steps so
orchestrator decisions take effect on subsequent calls.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..contracts.protocols import Orchestrator
from ..contracts.schema import OrchState
from ..models import WikiPage
from .sampler import GlobalOp, Sampler, SamplerState, sample_global

PolicyName = Literal["rule_policy", "llm_policy"]

_VALID_TIERS = ("S", "M", "L")
_MUTABLE_ROLES = ("extract", "write", "edit", "compact")


@dataclass
class PolicyRuntime:
    """Mutable view into tier + allocation settings for the LLM policy.

    The pipeline creates one instance at startup, populates it from the
    strategy config, and passes it to ``build_policy``. The LLM policy
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

    extract_tier: str = "S"
    write_tier: str = "M"
    edit_tier: str = "M"
    compact_tier: str = "S"
    # orchestrate_tier is locked at "L"; the LLM policy cannot change it
    orchestrate_tier: str = "L"
    # exploit_fraction in [0, 1]. None means "use schedule default".
    exploit_fraction: float | None = None
    # Reallocation epoch: incremented whenever the LLM policy sets a
    # new allocation, so the pipeline knows to re-split the remaining
    # budget on the next iteration.
    allocation_epoch: int = 0


@dataclass(frozen=True)
class PolicyContext:
    run_id: str
    n_pages: int
    n_candidates: int
    n_concepts: int
    n_people: int
    docs_covered: int
    docs_total: int


@dataclass(frozen=True)
class ExtractDecision:
    action: str
    batch: tuple[str, ...] = ()
    stop: bool = False
    meta: dict = field(default_factory=dict)


class DistillPolicy(Protocol):
    def next_extract(self, state: SamplerState, k: int, ctx: PolicyContext) -> ExtractDecision: ...
    def order_write_pages(
        self, pages: list[WikiPage], max_concepts: int, ctx: PolicyContext
    ) -> list[WikiPage]: ...
    def drain_events(self) -> list[dict]: ...


class RulePolicy:
    """Deterministic policy: sample with the configured sampler."""

    def __init__(self, sampler: Sampler) -> None:
        self._sampler = sampler
        self._events: list[dict] = []

    def next_extract(self, state: SamplerState, k: int, ctx: PolicyContext) -> ExtractDecision:
        batch = self._sampler.next_batch(state, k)
        decision = ExtractDecision(action="sample_batch", batch=tuple(batch), stop=not bool(batch))
        self._events.append(
            {
                "stage": "extract",
                "policy": "rule_policy",
                "action": decision.action,
                "n_chunks": len(batch),
                "stop": decision.stop,
                "n_pages": ctx.n_pages,
                "n_candidates": ctx.n_candidates,
            }
        )
        return decision

    def order_write_pages(
        self, pages: list[WikiPage], max_concepts: int, ctx: PolicyContext
    ) -> list[WikiPage]:
        ordered = pages[:max_concepts]
        self._events.append(
            {
                "stage": "write",
                "policy": "rule_policy",
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


class LlmPolicy:
    """Orchestrator-driven policy with deterministic sampler execution.

    The orchestrator chooses an action; this class executes it against the
    same sampler state used by rule strategies so telemetry is comparable.
    Control actions (``set_allocation``, ``set_tier``) mutate the shared
    ``PolicyRuntime`` so subsequent pipeline iterations pick up the change.

    Cost note: the orchestrator runs at tier L (opus) and a single
    decision costs ~30k haiku-equivalent tokens. Calling it on every
    extract batch would exhaust the budget on orchestration alone.
    Instead, an active sampling action (``walk_local``, ``jump_*``) is
    cached and re-used for up to ``persist_batches`` subsequent batches
    before re-querying the orchestrator. Control actions
    (``set_tier``, ``set_allocation``) and ``done`` are never cached.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        fallback_sampler: Sampler,
        runtime: PolicyRuntime | None = None,
        persist_batches: int = 8,
    ) -> None:
        self._orchestrator = orchestrator
        self._fallback_sampler = fallback_sampler
        self._runtime = runtime or PolicyRuntime()
        self._last_actions: list[str] = []
        self._events: list[dict] = []
        # Persist the last active action (jump_*, walk_local) for this
        # many consecutive batches before re-querying the orchestrator.
        self._persist_batches = max(1, persist_batches)
        self._cached_action_name: str | None = None
        self._cached_action_args: dict = {}
        self._batches_remaining: int = 0

    @property
    def runtime(self) -> PolicyRuntime:
        return self._runtime

    def next_extract(self, state: SamplerState, k: int, ctx: PolicyContext) -> ExtractDecision:
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
                sampler_snapshot=_build_sampler_snapshot(state),
            )
            action = self._orchestrator.step(orch_state)
            action_name = action.name
            action_args = dict(action.args or {})
            # Cache active sampling actions so we don't pay a tier-L call
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
        decision = self._execute_orch_action(state, k, action_name, action_args)
        event: dict = {
            "stage": "extract",
            "policy": "llm_policy",
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
        self, pages: list[WikiPage], max_concepts: int, ctx: PolicyContext
    ) -> list[WikiPage]:
        # v1: llm_policy controls exploration; write ordering stays deterministic.
        ordered = pages[:max_concepts]
        self._events.append(
            {
                "stage": "write",
                "policy": "llm_policy",
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

    def _execute_orch_action(
        self,
        state: SamplerState,
        k: int,
        name: str,
        args: dict,
    ) -> ExtractDecision:
        match name:
            case "done":
                return ExtractDecision(action=name, batch=(), stop=True)
            case "pick_chunks":
                raw_ids = args.get("chunk_ids") or []
                reason = str(args.get("reason", ""))
                # Deduplicate against already-seen chunks.
                novel = [cid for cid in raw_ids if cid not in state.seen_chunks]
                return ExtractDecision(
                    action=name,
                    batch=tuple(novel[:k]),
                    meta={"reason": reason, "n_requested": len(raw_ids), "n_novel": len(novel)},
                )
            case "jump_uniform" | "jump_pagerank" | "jump_gap":
                n_docs = max(1, _int_arg(args, "n_docs", 1))
                picks: list[str] = []
                op = {
                    "jump_uniform": GlobalOp.UNIFORM,
                    "jump_pagerank": GlobalOp.PAGERANK,
                    "jump_gap": GlobalOp.COVERAGE_GAP,
                }[name]
                for _ in range(n_docs):
                    picks.extend(sample_global(state, op))
                    if len(picks) >= k:
                        break
                return ExtractDecision(action=name, batch=tuple(picks[:k]), meta={"n_docs": n_docs})
            case "jump_figures":
                n = max(1, _int_arg(args, "k", k))
                picks_f: list[str] = []
                for _ in range(n):
                    got = sample_global(state, GlobalOp.FIGURES)
                    picks_f.extend(got)
                    if len(picks_f) >= k:
                        break
                return ExtractDecision(action=name, batch=tuple(picks_f[:k]))
            case "walk_local":
                n = max(1, _int_arg(args, "k", k))
                picks = self._fallback_sampler.next_batch(state, min(n, k))
                return ExtractDecision(action=name, batch=tuple(picks), stop=not bool(picks))
            case "set_allocation":
                # Mutate the runtime and return a no-op decision so the
                # extract loop consumes no chunks for this action. The
                # pipeline picks up the new exploit_fraction on the next
                # iteration via ``runtime.allocation_epoch``.
                frac = _float_arg(args, "exploit_fraction", -1.0)
                if 0.0 <= frac <= 1.0:
                    self._runtime.exploit_fraction = frac
                    self._runtime.allocation_epoch += 1
                return ExtractDecision(action=name, batch=(), meta={"exploit_fraction": frac})
            case "set_tier":
                # Mutate the per-role tier. Orchestrate tier is locked.
                role = str(args.get("role", "")).strip()
                tier = str(args.get("tier", "")).strip().upper()
                if role in _MUTABLE_ROLES and tier in _VALID_TIERS:
                    setattr(self._runtime, f"{role}_tier", tier)
                return ExtractDecision(action=name, batch=(), meta={"role": role, "tier": tier})
            case _:
                # Unknown action -> deterministic fallback.
                batch = self._fallback_sampler.next_batch(state, k)
                return ExtractDecision(
                    action="fallback_sample_batch",
                    batch=tuple(batch),
                    stop=not bool(batch),
                )


def _build_sampler_snapshot(state: SamplerState) -> dict:
    """Build the compact sampler snapshot for the orchestrator.

    Caps:
    - top_gap_chunks: top-20 by coverage residual
    - page_index: top-50 pages by evidence count (derived from seen_chunks count per page)
    - doc_coverage: all docs with any seen chunks
    - content_stats: aggregate counts

    Total payload is ~2-4 kB of JSON.
    """
    # top_gap_chunks: top-20 unseen chunks by coverage residual descending.
    residuals = getattr(state, "coverage_residuals", {})
    seen = getattr(state, "seen_chunks", set())
    doc_seen_counts = getattr(state, "doc_seen_counts", {})
    chunk_to_doc = getattr(state, "chunk_to_doc", {})
    if residuals:
        top_by_residual = sorted(
            (
                (cid, r)
                for cid, r in residuals.items()
                if cid not in seen
            ),
            key=lambda x: -x[1],
        )[:20]
    else:
        top_by_residual = []

    top_gap_chunks = [
        {
            "chunk_id": cid,
            "doc_id": chunk_to_doc.get(cid, ""),
            "residual": round(r, 4),
        }
        for cid, r in top_by_residual
    ]

    # doc_coverage: {doc_id: n_chunks_seen} for docs with at least one seen chunk.
    doc_coverage = {
        doc_id: count
        for doc_id, count in doc_seen_counts.items()
        if count > 0
    }

    # content_stats: aggregate counts from SamplerState.
    n_seen = len(seen)
    content_stats = {
        "n_chunks": len(chunk_to_doc),
        "n_seen": n_seen,
    }

    return {
        "top_gap_chunks": top_gap_chunks,
        "doc_coverage": doc_coverage,
        "content_stats": content_stats,
    }


def build_policy(
    *,
    name: PolicyName,
    sampler: Sampler,
    orchestrator: Orchestrator | None,
    runtime: PolicyRuntime | None = None,
) -> DistillPolicy:
    match name:
        case "rule_policy":
            return RulePolicy(sampler)
        case "llm_policy":
            if orchestrator is None:
                raise ValueError("llm_policy requires an orchestrator binding")
            return LlmPolicy(orchestrator, sampler, runtime=runtime)
        case _:
            raise ValueError(f"unknown policy: {name}")


def _int_arg(args: dict, key: str, default: int) -> int:
    val = args.get(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _float_arg(args: dict, key: str, default: float) -> float:
    val = args.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
