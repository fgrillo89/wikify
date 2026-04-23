"""Explorer protocol + LevyExplorer.

An explorer picks the next batch of chunk ids to feed the extractor. It is
parameterised by a triple ``(local_op, global_op, jump_rate)`` plus the
locked v1 constant ``chunks_per_landed_doc = 3``. The bootstrap rule
forces ``jump_rate = 1`` until at least one wiki page exists. Operator
dispatch is via small dispatch tables, not if/elif chains.

The module also owns the full action vocabulary (walk_local, jump_*,
pick_chunks, set_allocation, set_tier, done) via ``execute_action``.
Run modes (scripted/guided) decide WHICH action; this function executes HOW.

All corpus data access goes through the KnowledgeGraph fluent API. No
direct CorpusGraph, VectorStore, or flat-dict access. Similarity walks
use the KG's scoped similar_to() method (vector cosine via existing
embeddings).
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from ..config import CHUNKS_PER_LANDED_DOC
from ..types import ModelTier

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph
    from .strategy import RuntimeOverrides


class LocalOp(str, Enum):
    NONE = "none"
    SIMILARITY_WALK = "similarity_walk"
    REFINE_UNCERTAIN = "refine_uncertain"


class GlobalOp(str, Enum):
    UNIFORM = "uniform"
    PAGERANK = "pagerank"
    COVERAGE_GAP = "coverage_gap"
    FIGURES = "figures"


@dataclass
class ExplorerState:
    rng: random.Random
    kg: KnowledgeGraph
    # Cached lookups (built once from KG at init, not updated)
    chunks_by_doc: dict[str, list[str]] = field(default_factory=dict)
    abstract_chunk_by_doc: dict[str, str] = field(default_factory=dict)
    pagerank_doc: dict[str, float] = field(default_factory=dict)
    chunk_to_doc: dict[str, str] = field(default_factory=dict)
    caption_chunk_ids: set[str] = field(default_factory=set)
    # Mutable runtime state
    wiki_chunk_ids: set[str] = field(default_factory=set)
    pages_concept_evidence_chunks: list[str] = field(default_factory=list)
    seen_chunks: set[str] = field(default_factory=set)
    coverage_residuals: dict[str, float] = field(default_factory=dict)
    coverage_versions: dict[str, int] = field(default_factory=dict)
    coverage_heap: list[tuple[float, int, str]] = field(default_factory=list)
    caption_heap: list[tuple[float, int, str]] = field(default_factory=list)
    caption_versions: dict[str, int] = field(default_factory=dict)
    doc_seen_counts: dict[str, int] = field(default_factory=dict)

    @property
    def wiki_is_empty(self) -> bool:
        return not self.pages_concept_evidence_chunks


@dataclass(frozen=True)
class ExtractDecision:
    action: str
    batch: tuple[str, ...] = ()
    stop: bool = False
    meta: dict = field(default_factory=dict)


class Explorer(Protocol):
    def next_batch(self, state: ExplorerState, k: int) -> list[str]: ...


@dataclass(frozen=True)
class LevyExplorer:
    local_op: LocalOp
    global_op: GlobalOp
    jump_rate: float
    chunks_per_landed_doc: int = CHUNKS_PER_LANDED_DOC

    def next_batch(self, state: ExplorerState, k: int) -> list[str]:
        out: list[str] = []
        attempts = 0
        max_attempts = max(64, k * 32)
        while len(out) < k and attempts < max_attempts:
            attempts += 1
            do_global = state.wiki_is_empty or state.rng.random() < self.jump_rate
            picks = self._global(state) if do_global else [self._local(state)]
            if not picks or all(c is None for c in picks):
                picks = self._global(state)
            for c in picks:
                if c is None:
                    continue
                if c in state.seen_chunks:
                    continue
                out.append(c)
                state.seen_chunks.add(c)
                if len(out) >= k:
                    break
        return out

    def _local(self, state: ExplorerState) -> str | None:
        return explore_local(state, self.local_op)

    def _global(self, state: ExplorerState) -> list[str]:
        return explore_global(state, self.global_op, self.chunks_per_landed_doc)


def explore_local(state: ExplorerState, op: LocalOp) -> str | None:
    return _LOCAL_DISPATCH[op](state)


def explore_global(
    state: ExplorerState,
    op: GlobalOp,
    k_per_doc: int = CHUNKS_PER_LANDED_DOC,
) -> list[str]:
    return _GLOBAL_DISPATCH[op](state, k_per_doc)


_CAPTION_DEFAULT_RESIDUAL = 0.8
_CAPTION_NEAR_FLOOR = 0.4


def init_coverage_state(
    state: ExplorerState,
    chunk_ids: list[str],
    default_residual: float = 1.0,
) -> None:
    """Initialise the coverage residual map and heap.

    Caption chunks are seeded at ``_CAPTION_DEFAULT_RESIDUAL`` (0.8) so
    text chunks surface first; they also get their own ``caption_heap``
    for ``jump_figures``.
    """
    residuals: dict[str, float] = {}
    for cid in chunk_ids:
        if cid in state.caption_chunk_ids:
            residuals[cid] = _CAPTION_DEFAULT_RESIDUAL
        else:
            residuals[cid] = float(default_residual)

    # Boost residuals for chunks in highly-cited (foundation) papers.
    for source in state.kg.sources(kind="corpus").collect():
        if source.get("citation_count", 0) > 3:
            for cid in state.chunks_by_doc.get(source["id"], []):
                if cid in residuals:
                    residuals[cid] = min(residuals[cid] * 1.2, 1.0)

    state.coverage_residuals = residuals
    state.coverage_versions = {cid: 0 for cid in chunk_ids}
    state.coverage_heap = [(-r, 0, cid) for cid, r in residuals.items()]
    heapq.heapify(state.coverage_heap)
    caption_ids = [cid for cid in chunk_ids if cid in state.caption_chunk_ids]
    state.caption_versions = {cid: 0 for cid in caption_ids}
    state.caption_heap = [(-_CAPTION_DEFAULT_RESIDUAL, 0, cid) for cid in caption_ids]
    heapq.heapify(state.caption_heap)


def restore_coverage_state(
    state: ExplorerState,
    *,
    residuals: dict[str, float] | None,
    seen_chunks: set[str] | None,
    doc_seen_counts: dict[str, int] | None,
) -> None:
    """Restore persisted coverage memory from a previous epoch."""
    if residuals:
        state.coverage_residuals = dict(residuals)
    if seen_chunks:
        state.seen_chunks = set(seen_chunks)
    if doc_seen_counts:
        state.doc_seen_counts = {k: int(v) for k, v in doc_seen_counts.items()}
    state.coverage_versions = {cid: 0 for cid in state.coverage_residuals}
    state.coverage_heap = [(-float(r), 0, cid) for cid, r in state.coverage_residuals.items()]
    heapq.heapify(state.coverage_heap)
    caption_items = [
        (cid, r) for cid, r in state.coverage_residuals.items() if cid in state.caption_chunk_ids
    ]
    state.caption_versions = {cid: 0 for cid, _ in caption_items}
    state.caption_heap = [(-float(r), 0, cid) for cid, r in caption_items]
    heapq.heapify(state.caption_heap)


def apply_coverage_feedback(state: ExplorerState, chunk_id: str, *, as_evidence: bool) -> None:
    """Update residuals after reading or anchoring a chunk.

    Uses KG scoped similar_to for neighbor discount instead of pre-computed
    neighbor edges. Discounts same-document chunks progressively.
    """
    state.seen_chunks.add(chunk_id)
    _set_residual(state, chunk_id, 0.0)

    # Neighbor discount via vector similarity (top-5 similar chunks).
    text_near_floor = 0.2 if as_evidence else 0.35
    doc_id = state.chunk_to_doc.get(chunk_id)
    similar = state.kg.chunks().similar_to(chunk_id, top_k=5)
    for hit in similar:
        nb = hit["id"]
        cur = state.coverage_residuals.get(nb, 1.0)
        floor = _CAPTION_NEAR_FLOOR if nb in state.caption_chunk_ids else text_near_floor
        if cur > floor:
            _set_residual(state, nb, floor)

    if not doc_id:
        return
    count = state.doc_seen_counts.get(doc_id, 0) + 1
    state.doc_seen_counts[doc_id] = count
    if count == 1:
        doc_floor = 0.65
    elif count == 2:
        doc_floor = 0.50
    else:
        doc_floor = 0.35
    for dc in state.chunks_by_doc.get(doc_id, []):
        if dc == chunk_id:
            continue
        cur = state.coverage_residuals.get(dc, 1.0)
        if cur > doc_floor:
            _set_residual(state, dc, doc_floor)


def _set_residual(state: ExplorerState, chunk_id: str, value: float) -> None:
    state.coverage_residuals[chunk_id] = value
    v = state.coverage_versions.get(chunk_id, 0) + 1
    state.coverage_versions[chunk_id] = v
    heapq.heappush(state.coverage_heap, (-value, v, chunk_id))
    if chunk_id in state.caption_chunk_ids:
        cv = state.caption_versions.get(chunk_id, 0) + 1
        state.caption_versions[chunk_id] = cv
        heapq.heappush(state.caption_heap, (-value, cv, chunk_id))


# --- local dispatch ------------------------------------------------------


def _local_none(state: ExplorerState) -> str | None:
    return None


def _local_similarity_walk(state: ExplorerState) -> str | None:
    """Walk to a chunk similar to an existing evidence chunk.

    Uses KG scoped similar_to (vector cosine on existing embeddings).
    First tries local (same source), then global.
    """
    if not state.pages_concept_evidence_chunks:
        return None
    seed = state.rng.choice(state.pages_concept_evidence_chunks)
    doc_id = state.chunk_to_doc.get(seed)

    # Local: similar within same source
    if doc_id:
        hits = state.kg.source(doc_id).chunks().similar_to(seed, top_k=8)
        unseen = [h["id"] for h in hits if h["id"] not in state.seen_chunks]
        if unseen:
            return state.rng.choice(unseen)

    # Global fallback
    hits = state.kg.chunks().similar_to(seed, top_k=8)
    unseen = [h["id"] for h in hits if h["id"] not in state.seen_chunks]
    return state.rng.choice(unseen) if unseen else None


def _local_refine_uncertain(state: ExplorerState) -> str | None:
    if not state.coverage_residuals:
        return None
    items = sorted(state.coverage_residuals.items(), key=lambda kv: -kv[1])
    for cid, _ in items:
        if cid not in state.seen_chunks:
            return cid
    return None


_LOCAL_DISPATCH: dict[LocalOp, Callable[[ExplorerState], str | None]] = {
    LocalOp.NONE: _local_none,
    LocalOp.SIMILARITY_WALK: _local_similarity_walk,
    LocalOp.REFINE_UNCERTAIN: _local_refine_uncertain,
}


# --- global dispatch -----------------------------------------------------


def _doc_chunks_or_empty(state: ExplorerState, doc_id: str, k: int) -> list[str]:
    """Return up to k chunks for the doc: abstract first, then by ord."""
    chunks = list(state.chunks_by_doc.get(doc_id, []))
    if not chunks:
        return []
    abs_id = state.abstract_chunk_by_doc.get(doc_id)
    out: list[str] = []
    if abs_id and abs_id in chunks:
        out.append(abs_id)
        chunks = [c for c in chunks if c != abs_id]
    out.extend(chunks[: max(0, k - len(out))])
    return out


def _global_uniform(state: ExplorerState, k_per_doc: int) -> list[str]:
    docs = list(state.chunks_by_doc.keys())
    if not docs:
        return []
    doc = state.rng.choice(docs)
    return _doc_chunks_or_empty(state, doc, k_per_doc)


def _global_pagerank(state: ExplorerState, k_per_doc: int) -> list[str]:
    if not state.pagerank_doc:
        return _global_uniform(state, k_per_doc)
    docs, weights = zip(*state.pagerank_doc.items())
    doc = state.rng.choices(list(docs), weights=list(weights), k=1)[0]
    return _doc_chunks_or_empty(state, doc, k_per_doc)


def _global_coverage_gap(state: ExplorerState, _k_per_doc: int) -> list[str]:
    """Pop the highest-residual unseen TEXT chunk from the coverage heap.

    Caption chunks are skipped here so once text is exhausted the heap
    does not start feeding caption snippets to the extractor as if they
    were prose. Captions are reachable only via ``GlobalOp.FIGURES``
    (the dedicated caption heap).
    """
    if not state.coverage_residuals:
        return _global_uniform(state, _k_per_doc)
    while state.coverage_heap:
        neg, v, cid = heapq.heappop(state.coverage_heap)
        if state.coverage_versions.get(cid, -1) != v:
            continue
        if cid in state.seen_chunks:
            continue
        if cid in state.caption_chunk_ids:
            # Drop captions entirely from this heap; they live on the
            # caption_heap and the version bump means a stale entry will
            # be ignored even if re-pushed.
            continue
        heapq.heappush(state.coverage_heap, (neg, v, cid))
        return [cid]
    return []


def _global_figures(state: ExplorerState, _k_per_doc: int) -> list[str]:
    """Pop the highest-residual unseen caption chunk from the caption heap."""
    while state.caption_heap:
        neg, cv, cid = heapq.heappop(state.caption_heap)
        if state.caption_versions.get(cid, -1) != cv:
            continue
        if cid in state.seen_chunks:
            continue
        heapq.heappush(state.caption_heap, (neg, cv, cid))
        return [cid]
    return []


_GLOBAL_DISPATCH: dict[GlobalOp, Callable[[ExplorerState, int], list[str]]] = {
    GlobalOp.UNIFORM: _global_uniform,
    GlobalOp.PAGERANK: _global_pagerank,
    GlobalOp.COVERAGE_GAP: _global_coverage_gap,
    GlobalOp.FIGURES: _global_figures,
}


# --- action execution ----------------------------------------------------

_VALID_TIERS = {tier.value for tier in ModelTier}
_MUTABLE_ROLES = ("extract", "write", "edit", "compact")


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


def execute_action(
    name: str,
    args: dict,
    state: ExplorerState,
    k: int,
    explorer: Explorer,
    runtime: RuntimeOverrides | None = None,
) -> ExtractDecision:
    """Execute a named exploration action against the explorer state."""
    match name:
        case "done":
            return ExtractDecision(action=name, batch=(), stop=True)
        case "write_now":
            return ExtractDecision(action=name, batch=(), stop=True)
        case "sample_chunks" | "pick_chunks":
            raw_ids = args.get("chunk_ids") or []
            reason = str(args.get("reason", ""))
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
                picks.extend(explore_global(state, op))
                if len(picks) >= k:
                    break
            return ExtractDecision(action=name, batch=tuple(picks[:k]), meta={"n_docs": n_docs})
        case "jump_figures":
            n = max(1, _int_arg(args, "k", k))
            picks_f: list[str] = []
            for _ in range(n):
                got = explore_global(state, GlobalOp.FIGURES)
                picks_f.extend(got)
                if len(picks_f) >= k:
                    break
            return ExtractDecision(action=name, batch=tuple(picks_f[:k]))
        case "walk_local":
            n = max(1, _int_arg(args, "k", k))
            picks = explorer.next_batch(state, min(n, k))
            return ExtractDecision(action=name, batch=tuple(picks), stop=not bool(picks))
        case "set_allocation":
            frac = _float_arg(args, "exploit_fraction", -1.0)
            if runtime is not None and 0.0 <= frac <= 1.0:
                runtime.exploit_fraction = frac
                runtime.allocation_epoch += 1
            return ExtractDecision(action=name, batch=(), meta={"exploit_fraction": frac})
        case "set_tier":
            role = str(args.get("role", "")).strip()
            tier = str(args.get("tier", "")).strip().upper()
            if runtime is not None and role in _MUTABLE_ROLES and tier in _VALID_TIERS:
                setattr(runtime, f"{role}_tier", ModelTier(tier))
            return ExtractDecision(action=name, batch=(), meta={"role": role, "tier": tier})
        case _:
            batch = explorer.next_batch(state, k)
            return ExtractDecision(
                action="fallback_sample_batch",
                batch=tuple(batch),
                stop=not bool(batch),
            )


# --- snapshot for orchestrator -------------------------------------------


def build_snapshot(
    state: ExplorerState,
    *,
    budget_spent: float = 0.0,
    budget_remaining: float = 0.0,
    novelty_rate: float = 0.0,
    pages: list[dict] | None = None,
) -> dict:
    """Build the compact explorer snapshot for the orchestrator."""
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

    doc_coverage = {
        doc_id: count
        for doc_id, count in doc_seen_counts.items()
        if count > 0
    }

    n_seen = len(seen)
    content_stats = {
        "n_chunks": len(chunk_to_doc),
        "n_seen": n_seen,
    }

    # Budget context for guided mode
    budget = {
        "spent": round(budget_spent, 1),
        "remaining": round(budget_remaining, 1),
    }

    # Residual histogram: bin coverage residuals into 5 buckets
    bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    hist = [0] * (len(bins) - 1)
    for r in residuals.values():
        for i in range(len(bins) - 1):
            if bins[i] <= r < bins[i + 1] or (i == len(bins) - 2 and r == bins[i + 1]):
                hist[i] += 1
                break

    # Page summaries (compact)
    page_summaries = pages or []

    return {
        "top_gap_chunks": top_gap_chunks,
        "doc_coverage": doc_coverage,
        "content_stats": content_stats,
        "budget": budget,
        "novelty_rate": round(novelty_rate, 4),
        "residual_histogram": dict(zip(
            ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"],
            hist,
        )),
        "page_summaries": page_summaries,
    }


# --- semantic query helper -----------------------------------------------


def semantic_query_chunks(
    state: ExplorerState,
    query_vec: Any,
    k: int,
    scope: str = "all",
) -> list[dict]:
    """Return top-k chunks by cosine similarity via the KG.

    Args:
        state: current ExplorerState.
        query_vec: unit-norm float32 query embedding.
        k: number of results to return.
        scope: "all", "unseen", or "page:<id>".
    """
    vectors = state.kg._vectors
    if vectors is None or not vectors.ids:
        return []

    sims = vectors.cosine_to_all(query_vec)

    candidates: list[tuple[float, str]]
    if scope == "unseen":
        candidates = [
            (float(sims[i]), cid)
            for i, cid in enumerate(vectors.ids)
            if cid not in state.seen_chunks
        ]
    elif scope.startswith("page:"):
        page_id = scope[len("page:"):]
        candidates = [
            (float(sims[i]), cid)
            for i, cid in enumerate(vectors.ids)
            if state.chunk_to_doc.get(cid) == page_id
        ]
    else:
        candidates = [(float(sims[i]), cid) for i, cid in enumerate(vectors.ids)]

    candidates.sort(key=lambda x: -x[0])
    top = candidates[:k]

    return [
        {
            "chunk_id": cid,
            "doc_id": state.chunk_to_doc.get(cid, ""),
            "score": score,
            "is_seen": cid in state.seen_chunks,
        }
        for score, cid in top
    ]
