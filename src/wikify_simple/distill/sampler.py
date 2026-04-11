"""Sampler protocol + LevyMixSampler.

A sampler picks the next batch of chunk ids to feed the extractor. It is
parameterised by a triple ``(local_op, global_op, jump_rate)`` plus the
locked v1 constant ``chunks_per_landed_doc = 3``. The bootstrap rule
forces ``jump_rate = 1`` until at least one wiki page exists. Operator
dispatch is via small dispatch tables, not if/elif chains.
"""

import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..models import CorpusGraph
from ..store.vectors import VectorStore
from .config import CHUNKS_PER_LANDED_DOC


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
class SamplerState:
    rng: random.Random
    graph: CorpusGraph
    vectors: VectorStore
    chunks_by_doc: dict[str, list[str]]
    abstract_chunk_by_doc: dict[str, str]
    pagerank_doc: dict[str, float]
    neighbors_by_chunk: dict[str, tuple[str, ...]] = field(default_factory=dict)
    chunk_degree: dict[str, int] = field(default_factory=dict)
    chunk_to_doc: dict[str, str] = field(default_factory=dict)
    wiki_chunk_ids: set[str] = field(default_factory=set)  # chunks already in any page
    pages_concept_evidence_chunks: list[str] = field(default_factory=list)
    seen_chunks: set[str] = field(default_factory=set)
    caption_chunk_ids: set[str] = field(default_factory=set)
    coverage_residuals: dict[str, float] = field(default_factory=dict)
    coverage_versions: dict[str, int] = field(default_factory=dict)
    coverage_heap: list[tuple[float, int, str]] = field(default_factory=list)  # (-residual, v, cid)
    caption_heap: list[tuple[float, int, str]] = field(default_factory=list)  # (-residual, v, cid)
    caption_versions: dict[str, int] = field(default_factory=dict)
    doc_seen_counts: dict[str, int] = field(default_factory=dict)

    @property
    def wiki_is_empty(self) -> bool:
        return not self.pages_concept_evidence_chunks


class Sampler(Protocol):
    def next_batch(self, state: SamplerState, k: int) -> list[str]: ...


@dataclass(frozen=True)
class LevyMixSampler:
    local_op: LocalOp
    global_op: GlobalOp
    jump_rate: float
    chunks_per_landed_doc: int = CHUNKS_PER_LANDED_DOC

    def next_batch(self, state: SamplerState, k: int) -> list[str]:
        out: list[str] = []
        attempts = 0
        max_attempts = max(64, k * 32)
        while len(out) < k and attempts < max_attempts:
            attempts += 1
            do_global = state.wiki_is_empty or state.rng.random() < self.jump_rate
            picks = self._global(state) if do_global else [self._local(state)]
            if not picks or all(c is None for c in picks):
                # local couldn't produce — fall back to a global jump so we
                # don't infinite-loop on a wiki with no walk seeds yet.
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

    def _local(self, state: SamplerState) -> str | None:
        return sample_local(state, self.local_op)

    def _global(self, state: SamplerState) -> list[str]:
        return sample_global(state, self.global_op, self.chunks_per_landed_doc)


def sample_local(state: SamplerState, op: LocalOp) -> str | None:
    return _LOCAL_DISPATCH[op](state)


def sample_global(
    state: SamplerState,
    op: GlobalOp,
    k_per_doc: int = CHUNKS_PER_LANDED_DOC,
) -> list[str]:
    return _GLOBAL_DISPATCH[op](state, k_per_doc)


_CAPTION_DEFAULT_RESIDUAL = 0.8
_CAPTION_NEAR_FLOOR = 0.4


def init_coverage_state(
    state: SamplerState,
    chunk_ids: list[str],
    default_residual: float = 1.0,
) -> None:
    """Initialise the coverage residual map and heap.

    ``coverage_gap`` ranks chunks by residual descending; higher means less
    covered by the current wiki evidence set. The residual model is
    graph-local and cheap: unseen chunks start at ``default_residual`` and
    are progressively discounted as nearby chunks are read/anchored.

    Caption chunks (those in ``state.caption_chunk_ids``) are seeded at
    ``_CAPTION_DEFAULT_RESIDUAL`` (0.8) so text chunks surface first; they
    also get their own dedicated ``caption_heap`` for ``jump_figures``.
    """
    residuals: dict[str, float] = {}
    for cid in chunk_ids:
        if cid in state.caption_chunk_ids:
            residuals[cid] = _CAPTION_DEFAULT_RESIDUAL
        else:
            residuals[cid] = float(default_residual)
    state.coverage_residuals = residuals
    state.coverage_versions = {cid: 0 for cid in chunk_ids}
    state.coverage_heap = [(-r, 0, cid) for cid, r in residuals.items()]
    heapq.heapify(state.coverage_heap)
    # Dedicated caption heap for jump_figures.
    caption_ids = [cid for cid in chunk_ids if cid in state.caption_chunk_ids]
    state.caption_versions = {cid: 0 for cid in caption_ids}
    state.caption_heap = [(-_CAPTION_DEFAULT_RESIDUAL, 0, cid) for cid in caption_ids]
    heapq.heapify(state.caption_heap)


def restore_coverage_state(
    state: SamplerState,
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
    # Rebuild heap and versions from the restored residual map.
    state.coverage_versions = {cid: 0 for cid in state.coverage_residuals}
    state.coverage_heap = [(-float(r), 0, cid) for cid, r in state.coverage_residuals.items()]
    heapq.heapify(state.coverage_heap)
    # Rebuild caption heap from restored residuals.
    caption_items = [
        (cid, r) for cid, r in state.coverage_residuals.items() if cid in state.caption_chunk_ids
    ]
    state.caption_versions = {cid: 0 for cid, _ in caption_items}
    state.caption_heap = [(-float(r), 0, cid) for cid, r in caption_items]
    heapq.heapify(state.caption_heap)


def apply_coverage_feedback(state: SamplerState, chunk_id: str, *, as_evidence: bool) -> None:
    """Update residuals after reading or anchoring a chunk.

    - seen chunk residual -> 0.0
    - neighbour residuals discounted (stronger discount when chunk became evidence)
    - same-document chunk residuals discounted progressively with repeated reads
    """
    state.seen_chunks.add(chunk_id)
    _set_residual(state, chunk_id, 0.0)

    text_near_floor = 0.2 if as_evidence else 0.35
    for nb in state.neighbors_by_chunk.get(chunk_id, ()):
        cur = state.coverage_residuals.get(nb, 1.0)
        # Caption neighbors are discounted less aggressively than text-to-text.
        floor = _CAPTION_NEAR_FLOOR if nb in state.caption_chunk_ids else text_near_floor
        if cur > floor:
            _set_residual(state, nb, floor)

    doc_id = state.chunk_to_doc.get(chunk_id)
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


def _set_residual(state: SamplerState, chunk_id: str, value: float) -> None:
    state.coverage_residuals[chunk_id] = value
    v = state.coverage_versions.get(chunk_id, 0) + 1
    state.coverage_versions[chunk_id] = v
    heapq.heappush(state.coverage_heap, (-value, v, chunk_id))
    if chunk_id in state.caption_chunk_ids:
        cv = state.caption_versions.get(chunk_id, 0) + 1
        state.caption_versions[chunk_id] = cv
        heapq.heappush(state.caption_heap, (-value, cv, chunk_id))


# --- local dispatch ------------------------------------------------------


def _local_none(state: SamplerState) -> str | None:
    return None


def _local_similarity_walk(state: SamplerState) -> str | None:
    if not state.pages_concept_evidence_chunks:
        return None
    seed = state.rng.choice(state.pages_concept_evidence_chunks)
    neighbours = [b for b in state.neighbors_by_chunk.get(seed, ()) if b not in state.seen_chunks]
    return state.rng.choice(neighbours) if neighbours else None


def _local_refine_uncertain(state: SamplerState) -> str | None:
    # Approximation: pick from highest residuals
    if not state.coverage_residuals:
        return None
    items = sorted(state.coverage_residuals.items(), key=lambda kv: -kv[1])
    for cid, _ in items:
        if cid not in state.seen_chunks:
            return cid
    return None


_LOCAL_DISPATCH: dict[LocalOp, Callable[[SamplerState], str | None]] = {
    LocalOp.NONE: _local_none,
    LocalOp.SIMILARITY_WALK: _local_similarity_walk,
    LocalOp.REFINE_UNCERTAIN: _local_refine_uncertain,
}


# --- global dispatch -----------------------------------------------------


def _doc_chunks_or_empty(state: SamplerState, doc_id: str, k: int) -> list[str]:
    """Return up to k chunks for the doc: abstract first, then highest-degree."""
    chunks = list(state.chunks_by_doc.get(doc_id, []))
    if not chunks:
        return []
    abs_id = state.abstract_chunk_by_doc.get(doc_id)
    out: list[str] = []
    if abs_id and abs_id in chunks:
        out.append(abs_id)
        chunks = [c for c in chunks if c != abs_id]
    chunks.sort(key=lambda c: -state.chunk_degree.get(c, 0))
    out.extend(chunks[: max(0, k - len(out))])
    return out


def _global_uniform(state: SamplerState, k_per_doc: int) -> list[str]:
    docs = list(state.chunks_by_doc.keys())
    if not docs:
        return []
    doc = state.rng.choice(docs)
    return _doc_chunks_or_empty(state, doc, k_per_doc)


def _global_pagerank(state: SamplerState, k_per_doc: int) -> list[str]:
    if not state.pagerank_doc:
        return _global_uniform(state, k_per_doc)
    docs, weights = zip(*state.pagerank_doc.items())
    doc = state.rng.choices(list(docs), weights=list(weights), k=1)[0]
    return _doc_chunks_or_empty(state, doc, k_per_doc)


def _global_coverage_gap(state: SamplerState, _k_per_doc: int) -> list[str]:
    if not state.coverage_residuals:
        return _global_uniform(state, _k_per_doc)
    while state.coverage_heap:
        neg, v, cid = heapq.heappop(state.coverage_heap)
        if state.coverage_versions.get(cid, -1) != v:
            continue
        if cid in state.seen_chunks:
            continue
        # Keep the selected candidate in the heap with the same score so
        # repeated calls remain stable if the caller skips it.
        heapq.heappush(state.coverage_heap, (neg, v, cid))
        return [cid]
    return []


def _global_figures(state: SamplerState, _k_per_doc: int) -> list[str]:
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


_GLOBAL_DISPATCH: dict[GlobalOp, Callable[[SamplerState, int], list[str]]] = {
    GlobalOp.UNIFORM: _global_uniform,
    GlobalOp.PAGERANK: _global_pagerank,
    GlobalOp.COVERAGE_GAP: _global_coverage_gap,
    GlobalOp.FIGURES: _global_figures,
}
