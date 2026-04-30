"""Fluent knowledge graph API for agent traversal and scoped vector search.

Agents chain typed methods, never touch NetworkX or raw indexes.
Backend is NetworkX + dict indexes internally, swappable to FalkorDB later.

Usage::

    kg = KnowledgeGraph(backend, vectors, embed_fn)
    kg.source("paper_X").cited_by().sections(type="conclusions").chunks().collect()
    kg.author("smith_j").sources().top(10, by="pagerank").collect()
    kg.source("Y").cited_by().chunks().search("concept X", top_k=5)
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

_MARKER_RE = re.compile(r"\[(\d+(?:\s*[-‐-―,]\s*\d+)*)\]")

if TYPE_CHECKING:
    import networkx as nx

    from wikify.corpus.vectors import VectorStore


# ---------------------------------------------------------------------------
# Citation marker parsing (standalone, no class needed)
# ---------------------------------------------------------------------------


def parse_citation_markers(text: str) -> list[int]:
    """Parse citation markers like [1-3], [4,5] from text.

    Returns sorted unique ordinals. Standalone function -- does not need
    a RefLookup or any corpus state.
    """
    nums: list[int] = []
    # Range separators: hyphen plus the unicode dash family ‐‑‒–—― (U+2010..U+2015).
    range_seps = "-‐‑‒–—―"
    range_re = re.compile(rf"[{range_seps}]")
    for m in _MARKER_RE.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip()
            split = range_re.split(part, maxsplit=1)
            if len(split) == 2:
                try:
                    nums.extend(range(int(split[0].strip()), int(split[1].strip()) + 1))
                except ValueError:
                    pass
            else:
                try:
                    nums.append(int(part))
                except ValueError:
                    pass
    return sorted(set(nums))


# ---------------------------------------------------------------------------
# Node type constants
# ---------------------------------------------------------------------------

SOURCE = "source"
AUTHOR = "author"
CHUNK = "chunk"
SECTION = "section"
FIGURE = "figure"
EQUATION = "equation"


# ---------------------------------------------------------------------------
# NetworkX backend -- internal, never exposed to agents
# ---------------------------------------------------------------------------


@dataclass
class NetworkXBackend:
    """NetworkX graph backend with inverted dict indexes."""

    G: nx.MultiDiGraph

    # Hot-path indexes (rebuilt from G at load time)
    _cited_by: dict[str, set[str]] = field(default_factory=dict)
    _references: dict[str, set[str]] = field(default_factory=dict)
    _sources_of: dict[str, set[str]] = field(default_factory=dict)
    _authors_of: dict[str, set[str]] = field(default_factory=dict)
    _coauthors: dict[str, set[str]] = field(default_factory=dict)
    _sections_of: dict[str, list[str]] = field(default_factory=dict)
    _chunks_of_source: dict[str, list[str]] = field(default_factory=dict)
    _chunks_of_section: dict[str, list[str]] = field(default_factory=dict)
    _figures_of: dict[str, list[str]] = field(default_factory=dict)
    _equations_of: dict[str, list[str]] = field(default_factory=dict)
    _equations_in_chunk: dict[str, list[str]] = field(default_factory=dict)
    _figures_near_chunk: dict[str, list[str]] = field(default_factory=dict)
    _chunks_near_figure: dict[str, list[str]] = field(default_factory=dict)
    _chunks_with_equation: dict[str, list[str]] = field(default_factory=dict)
    _pagerank: dict[str, float] = field(default_factory=dict)
    _h_index: dict[str, int] = field(default_factory=dict)
    _ord_refs: dict[str, dict[int, str]] = field(default_factory=dict)

    def rebuild_indexes(self) -> None:
        """O(E) scan to populate all inverted indexes from the graph."""
        g = self.G
        self._cited_by.clear()
        self._references.clear()
        self._sources_of.clear()
        self._authors_of.clear()
        self._coauthors.clear()
        self._sections_of.clear()
        self._chunks_of_source.clear()
        self._chunks_of_section.clear()
        self._figures_of.clear()
        self._equations_of.clear()
        self._equations_in_chunk.clear()
        self._figures_near_chunk.clear()
        self._chunks_near_figure.clear()
        self._chunks_with_equation.clear()
        self._pagerank.clear()
        self._h_index.clear()
        self._ord_refs.clear()

        for u, v, data in g.edges(data=True):
            kind = data.get("kind", "")
            if kind == "CITES":
                self._references.setdefault(u, set()).add(v)
                self._cited_by.setdefault(v, set()).add(u)
            elif kind == "AUTHORED_BY":
                self._authors_of.setdefault(u, set()).add(v)
                self._sources_of.setdefault(v, set()).add(u)
            elif kind == "COLLABORATED":
                self._coauthors.setdefault(u, set()).add(v)
                self._coauthors.setdefault(v, set()).add(u)
            elif kind == "CONTAINS_SECTION":
                self._sections_of.setdefault(u, []).append(v)
            elif kind == "CONTAINS_CHUNK":
                self._chunks_of_source.setdefault(u, []).append(v)
            elif kind == "CHUNK_IN_SECTION":
                self._chunks_of_section.setdefault(v, []).append(u)
            elif kind == "CONTAINS_FIGURE":
                self._figures_of.setdefault(u, []).append(v)
            elif kind == "CONTAINS_EQUATION":
                self._equations_of.setdefault(u, []).append(v)
            elif kind == "EQUATION_IN_CHUNK":
                self._equations_in_chunk.setdefault(v, []).append(u)
                self._chunks_with_equation.setdefault(u, []).append(v)
            elif kind == "FIGURE_NEAR_CHUNK":
                self._figures_near_chunk.setdefault(v, []).append(u)
                self._chunks_near_figure.setdefault(u, []).append(v)

        # Node-level metrics
        for nid, ndata in g.nodes(data=True):
            if "pagerank" in ndata:
                self._pagerank[nid] = ndata["pagerank"]
            if "h_index" in ndata:
                self._h_index[nid] = ndata["h_index"]
            if "ord_refs" in ndata:
                self._ord_refs[nid] = {
                    int(k): v for k, v in ndata["ord_refs"].items()
                }

    def node(self, nid: str) -> dict:
        """Return node attributes as a dict with 'id' included."""
        attrs = dict(self.G.nodes[nid])
        attrs["id"] = nid
        return attrs

    def has_node(self, nid: str) -> bool:
        return nid in self.G

    def nodes_of_type(self, ntype: str) -> set[str]:
        return {
            nid for nid, d in self.G.nodes(data=True)
            if d.get("type") == ntype
        }

    def neighbors(self, nid: str, hops: int = 1) -> set[str]:
        """Undirected N-hop neighbors."""
        if nid not in self.G:
            return set()
        undirected = self.G.to_undirected(as_view=True)
        result: set[str] = set()
        frontier = {nid}
        for _ in range(hops):
            next_frontier: set[str] = set()
            for n in frontier:
                for nb in undirected.neighbors(n):
                    if nb != nid and nb not in result:
                        next_frontier.add(nb)
            result |= next_frontier
            frontier = next_frontier
        return result


# ---------------------------------------------------------------------------
# QueryBuilder -- lazy, composable, fluent
# ---------------------------------------------------------------------------


class QueryBuilder:
    """Lazy query builder over the knowledge graph.

    Each traversal returns a new QueryBuilder scoped to the result set.
    Nothing executes until a terminal (.collect(), .ids(), .count(), etc.).

    When the queryset's node type is ``CHUNK``, the terminal operations
    apply a soft boilerplate filter by default — chunks whose
    ``is_boilerplate`` attribute is True are excluded. This keeps the
    canonical access path (the fluent API) clean for every consumer
    (model agents, baseline, evidence retrieval, eval) without each
    one having to know the filter exists. To opt back in (debugging,
    boilerplate audits), pass ``include_boilerplate=True`` to the
    method that produces the queryset (``kg.chunks(...)``,
    ``source.chunks(...)``) or call ``.with_boilerplate()`` on an
    existing builder.
    """

    __slots__ = ("_kg", "_ids", "_type", "_include_boilerplate", "_order")

    def __init__(
        self,
        kg: KnowledgeGraph,
        node_ids: set[str],
        node_type: str | None = None,
        *,
        include_boilerplate: bool = False,
        order: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._kg = kg
        self._ids = frozenset(node_ids)
        self._type = node_type
        self._include_boilerplate = include_boilerplate
        # Optional metric-rank order, set by `top()` and propagated by
        # filters that narrow the same id-space (`where`/`match`/`since`/
        # `of_type`/`with_boilerplate`). Traversals that change the
        # id-space (cited_by, chunks, etc.) drop it — there is no
        # meaningful order across a different node type.
        self._order = tuple(order) if order is not None else None

    def _ordered_ids(self) -> list[str]:
        """Return ids as a list, in metric-rank order if `_order` is set,
        else alphabetically. Boilerplate filter is applied either way.
        """
        filtered = self._filter_boilerplate(self._ids)
        if self._order is not None:
            return [nid for nid in self._order if nid in filtered]
        return sorted(filtered)

    # ---- Boilerplate filter ----

    def with_boilerplate(self) -> QueryBuilder:
        """Return a queryset that includes boilerplate-flagged chunks."""
        return QueryBuilder(
            self._kg, set(self._ids), self._type,
            include_boilerplate=True,
            order=self._order,
        )

    def _filter_boilerplate(self, ids: set[str] | frozenset[str]) -> set[str]:
        """Drop boilerplate chunks unless include_boilerplate is True."""
        if self._include_boilerplate or self._type != CHUNK:
            return set(ids)
        backend = self._kg._backend
        return {
            cid for cid in ids
            if not backend.G.nodes.get(cid, {}).get("is_boilerplate", False)
        }

    # ---- Traversal helpers ----

    def _follow(self, index: dict, node_type: str, *, exclude_self: bool = False) -> QueryBuilder:
        """Traverse an inverted index: union all values for current IDs."""
        result: set[str] = set()
        for nid in self._ids:
            hit = index.get(nid)
            if hit is not None:
                result |= hit if isinstance(hit, set) else set(hit)
        if exclude_self:
            result -= self._ids
        return QueryBuilder(self._kg, result, node_type)

    # ---- Traversal (returns new QueryBuilder) ----

    def cited_by(self) -> QueryBuilder:
        """Sources that cite these sources."""
        return self._follow(self._kg._backend._cited_by, SOURCE)

    def references(self, ords: list[int] | None = None) -> QueryBuilder:
        """Sources cited by these sources. Optionally filter by ordinal."""
        if ords is not None:
            result: set[str] = set()
            ord_idx = self._kg._backend._ord_refs
            for sid in self._ids:
                om = ord_idx.get(sid, {})
                for n in ords:
                    target = om.get(n)
                    if target:
                        result.add(target)
            return QueryBuilder(self._kg, result, SOURCE)
        return self._follow(self._kg._backend._references, SOURCE)

    def neighborhood(self, hops: int = 1) -> QueryBuilder:
        """N-hop undirected graph neighbors."""
        result: set[str] = set()
        for nid in self._ids:
            result |= self._kg._backend.neighbors(nid, hops)
        return QueryBuilder(self._kg, result, None)

    def authors(self) -> QueryBuilder:
        """Authors of these sources."""
        return self._follow(self._kg._backend._authors_of, AUTHOR)

    def sources(self) -> QueryBuilder:
        """Sources by these authors."""
        return self._follow(self._kg._backend._sources_of, SOURCE)

    def coauthors(self) -> QueryBuilder:
        """Co-authors of these authors."""
        return self._follow(self._kg._backend._coauthors, AUTHOR, exclude_self=True)

    def sections(self, type: str | None = None) -> QueryBuilder:
        """Sections of these sources. Optionally filter by section type."""
        qb = self._follow(self._kg._backend._sections_of, SECTION)
        if type is not None:
            qb = qb.where(section_type=type)
        return qb

    def chunks(self, *, include_boilerplate: bool = False) -> QueryBuilder:
        """Chunks of these sources or sections.

        Boilerplate-flagged chunks are excluded by default. Pass
        ``include_boilerplate=True`` to see them (debugging / audit).
        """
        result: set[str] = set()
        backend = self._kg._backend
        if self._type == SECTION:
            idx = backend._chunks_of_section
            for sid in self._ids:
                result.update(idx.get(sid, []))
        else:
            for sid in self._ids:
                result.update(backend._chunks_of_source.get(sid, []))
                result.update(backend._chunks_of_section.get(sid, []))
        return QueryBuilder(
            self._kg, result, CHUNK,
            include_boilerplate=include_boilerplate,
        )

    def abstract_chunk(self) -> dict | None:
        """The canonical abstract chunk for the (first) source in the set.

        Reads the ``section_type == "abstract"`` invariant set at ingest
        by :func:`abstract_tagger.tag_abstracts` — exactly one chunk per
        body-bearing source has the abstract tag. Returns the chunk dict,
        or None if the source has no body content (rare).

        Use:
            chunk = kg.source(doc_id).abstract_chunk()
        """
        match = self.chunks().where(section_type="abstract").first()
        return match

    def figures(self) -> QueryBuilder:
        """Figures of these sources."""
        return self._follow(self._kg._backend._figures_of, FIGURE)

    def equations(self) -> QueryBuilder:
        """Equations of these sources."""
        return self._follow(self._kg._backend._equations_of, EQUATION)

    def math_equations(self) -> QueryBuilder:
        """Mathematical equations (excluding chemical formulas)."""
        return self.equations().where(is_chemical=False)

    def chemical_formulas(self) -> QueryBuilder:
        """Chemical formulas only."""
        return self.equations().where(is_chemical=True)

    def nearby_figures(self) -> QueryBuilder:
        """Figures linked to these chunks via FIGURE_NEAR_CHUNK edges."""
        return self._follow(self._kg._backend._figures_near_chunk, FIGURE)

    def nearby_equations(self) -> QueryBuilder:
        """Equations in these chunks via EQUATION_IN_CHUNK edges."""
        return self._follow(self._kg._backend._equations_in_chunk, EQUATION)

    # ---- Filters (returns narrowed QueryBuilder) ----

    def where(self, **kwargs: object) -> QueryBuilder:
        """Filter current set by node attributes."""
        backend = self._kg._backend
        result: set[str] = set()
        for nid in self._ids:
            if nid not in backend.G:
                continue
            attrs = backend.G.nodes[nid]
            match = True
            for k, v in kwargs.items():
                if attrs.get(k) != v:
                    match = False
                    break
            if match:
                result.add(nid)
        return QueryBuilder(self._kg, result, self._type, order=self._order)

    def of_type(self, kind: str) -> QueryBuilder:
        """Filter by node type."""
        backend = self._kg._backend
        result = {
            nid for nid in self._ids
            if nid in backend.G and backend.G.nodes[nid].get("type") == kind
        }
        return QueryBuilder(self._kg, result, kind, order=self._order)

    def match(self, field: str, query: str) -> QueryBuilder:
        """Filter nodes where `field` contains `query` (case-insensitive substring)."""
        backend = self._kg._backend
        q = query.lower()
        result = {
            nid for nid in self._ids
            if nid in backend.G
            and q in str(backend.G.nodes[nid].get(field, "")).lower()
        }
        return QueryBuilder(self._kg, result, self._type, order=self._order)

    def since(self, year: int) -> QueryBuilder:
        """Filter sources by year >= N."""
        backend = self._kg._backend
        result = {
            nid for nid in self._ids
            if nid in backend.G and (backend.G.nodes[nid].get("year") or 0) >= year
        }
        return QueryBuilder(self._kg, result, self._type, order=self._order)

    def top(self, n: int, by: str) -> QueryBuilder:
        """Top N by a metric (pagerank, year, citation_count, h_index).

        The returned builder remembers the metric ranking. Terminals
        (`collect`/`ids`/`first`) honour it; same-id-space filters
        (`where`/`match`/`since`/`of_type`/`with_boilerplate`) propagate
        it; traversals that change the id-space (`cited_by`/`chunks`
        /etc.) drop it.
        """
        backend = self._kg._backend
        scored: list[tuple[float, str]] = []
        for nid in self._ids:
            if nid not in backend.G:
                continue
            attrs = backend.G.nodes[nid]
            if by == "pagerank":
                val = backend._pagerank.get(nid, 0.0)
            elif by == "h_index":
                val = float(backend._h_index.get(nid, 0))
            else:
                val = float(attrs.get(by, 0) or 0)
            scored.append((val, nid))
        scored.sort(key=lambda t: -t[0])
        ordered = [nid for _, nid in scored[:n]]
        return QueryBuilder(
            self._kg, set(ordered), self._type, order=ordered,
        )

    # ---- Vector search (scoped to current set) ----

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Execute vector search scoped to the current node IDs.

        Embeds the query text, computes cosine similarity against all
        vectors, then filters to only IDs in the current set (or their
        chunks' source_ids). Returns dicts with id, source_id, score.
        """
        vectors = self._kg._vectors
        embed_fn = self._kg._embed_fn
        if vectors is None or embed_fn is None:
            return []

        # Determine which chunk IDs to scope to
        scope_ids = self._resolve_chunk_scope()
        if not scope_ids:
            return []

        # Embed query
        vecs = embed_fn([query])
        if vecs.shape[0] == 0:
            return []
        qvec = vecs[0]

        # Compute similarities against all, then filter
        sims = vectors.cosine_to_all(qvec)
        # Build set of valid vector-store indexes
        valid_idx: list[int] = []
        for i, cid in enumerate(vectors.ids):
            if cid in scope_ids:
                valid_idx.append(i)

        if not valid_idx:
            return []

        # Sort valid indexes by similarity descending
        scored = [(sims[i], i) for i in valid_idx]
        scored.sort(key=lambda t: -t[0])

        results: list[dict] = []
        backend = self._kg._backend
        for score, idx in scored[:top_k]:
            cid = vectors.ids[idx]
            node_data = backend.node(cid) if backend.has_node(cid) else {"id": cid}
            node_data["score"] = float(score)
            results.append(node_data)
        self._kg._trace.log(
            "search", {"query": query[:80], "top_k": top_k},
            len(scope_ids), len(results),
            [r["id"] for r in results[:5]],
        )
        return results

    def similar_to(self, chunk_id: str, top_k: int = 10) -> list[dict]:
        """Find chunks similar to an existing chunk by vector cosine.

        Uses the chunk's existing embedding -- no re-embedding needed.
        Scoped to the current set (if current set is sources/sections,
        resolves to their chunks first). Excludes the seed chunk itself.
        """
        vectors = self._kg._vectors
        if vectors is None:
            return []
        try:
            seed_vec = vectors.vector(chunk_id)
        except (KeyError, IndexError):
            return []

        scope_ids = self._resolve_chunk_scope()
        scope_ids.discard(chunk_id)
        if not scope_ids:
            return []

        sims = vectors.cosine_to_all(seed_vec)
        valid_idx = [i for i, cid in enumerate(vectors.ids) if cid in scope_ids]
        if not valid_idx:
            return []

        scored = sorted([(sims[i], i) for i in valid_idx], key=lambda t: -t[0])
        backend = self._kg._backend
        results: list[dict] = []
        for score, idx in scored[:top_k]:
            cid = vectors.ids[idx]
            node_data = backend.node(cid) if backend.has_node(cid) else {"id": cid}
            node_data["score"] = float(score)
            results.append(node_data)
        self._kg._trace.log(
            "similar_to", {"chunk_id": chunk_id, "top_k": top_k},
            len(scope_ids), len(results),
            [r["id"] for r in results[:5]],
        )
        return results

    def _resolve_chunk_scope(self) -> set[str]:
        """Resolve the current set to chunk IDs for vector search.

        Applies the soft boilerplate filter (unless include_boilerplate
        was set on this query) so vector search results never surface
        boilerplate chunks. Cross-type traversals (source → chunks via
        scope resolution) get the same default behaviour as direct
        ``.chunks()`` calls.
        """
        if self._type == CHUNK:
            chunk_ids = set(self._ids)
        else:
            backend = self._kg._backend
            chunk_ids = set()
            for nid in self._ids:
                if not backend.has_node(nid):
                    continue
                ntype = backend.G.nodes[nid].get("type")
                if ntype == CHUNK:
                    chunk_ids.add(nid)
                elif ntype == SOURCE:
                    chunk_ids.update(backend._chunks_of_source.get(nid, []))
                elif ntype == SECTION:
                    chunk_ids.update(backend._chunks_of_section.get(nid, []))
                elif ntype == FIGURE:
                    chunk_ids.update(backend._chunks_near_figure.get(nid, []))
                elif ntype == EQUATION:
                    chunk_ids.update(backend._chunks_with_equation.get(nid, []))
        # Apply soft boilerplate filter (no-op if include_boilerplate=True
        # or queryset isn't chunks). Vector-search consumers (similar_to,
        # search, the writer's evidence retrieval, the explorer's local
        # walks) all flow through here, so this is the chokepoint.
        if not self._include_boilerplate:
            backend = self._kg._backend
            chunk_ids = {
                cid for cid in chunk_ids
                if not backend.G.nodes.get(cid, {}).get("is_boilerplate", False)
            }
        return chunk_ids

    # ---- Terminals (execute and return) ----

    def collect(self) -> list[dict]:
        """Materialize all nodes as dicts.

        Order: metric-rank if `top()` was called upstream, else
        alphabetical by node id.
        """
        backend = self._kg._backend
        ordered = self._ordered_ids()
        result = [backend.node(nid) for nid in ordered if backend.has_node(nid)]
        self._kg._trace.log(
            "collect", {}, len(ordered), len(result),
            [r["id"] for r in result[:5]],
        )
        return result

    def ids(self) -> list[str]:
        """Return just the node IDs.

        Order: metric-rank if `top()` was called upstream, else
        alphabetical.
        """
        return self._ordered_ids()

    def count(self) -> int:
        """Count matches."""
        return len(self._filter_boilerplate(self._ids))

    def first(self) -> dict | None:
        """First result or None.

        With `top()` upstream, returns the highest-ranked node;
        otherwise the alphabetically-first node.
        """
        ordered = self._ordered_ids()
        if not ordered:
            return None
        backend = self._kg._backend
        nid = ordered[0]
        return backend.node(nid) if backend.has_node(nid) else None

    def exists(self) -> bool:
        """Any matches?"""
        return len(self._filter_boilerplate(self._ids)) > 0

    def titles(self) -> list[str]:
        """Return the title (or id) of each node as a flat list.

        For sources: returns the ``title`` attribute.
        For authors: returns the node id (which is the author name).
        For other types: returns whatever ``title`` is set to, falling
        back to the node id.
        """
        backend = self._kg._backend
        out: list[str] = []
        for nid in sorted(self._ids):
            if not backend.has_node(nid):
                continue
            attrs = backend.G.nodes[nid]
            out.append(str(attrs.get("title", nid)))
        return out

    # ---- Metrics on current set ----

    def pagerank(self) -> dict[str, float]:
        """PageRank scores for the current set."""
        pr = self._kg._backend._pagerank
        return {nid: pr.get(nid, 0.0) for nid in self._ids}

    def citation_count(self) -> dict[str, int]:
        """Citation counts for the current set."""
        cb = self._kg._backend._cited_by
        return {nid: len(cb.get(nid, set())) for nid in self._ids}


# ---------------------------------------------------------------------------
# Trace context -- lightweight exploration logging
# ---------------------------------------------------------------------------


@dataclass
class TraceEntry:
    """One logged KG operation (terminal or search call)."""

    timestamp: str
    caller: str
    method: str
    args: dict = field(default_factory=dict)
    input_count: int = 0
    output_count: int = 0
    output_sample: list[str] = field(default_factory=list)


@dataclass
class TraceContext:
    """Append-only log of KG operations. Enabled per-run."""

    entries: list[TraceEntry] = field(default_factory=list)
    enabled: bool = False
    caller: str = ""

    def log(
        self,
        method: str,
        args: dict,
        input_count: int,
        output_count: int,
        output_sample: list[str],
    ) -> None:
        if not self.enabled:
            return
        self.entries.append(TraceEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            caller=self.caller,
            method=method,
            args=args,
            input_count=input_count,
            output_count=output_count,
            output_sample=output_sample[:5],
        ))

    def save(self, path: Path) -> None:
        """Append entries to a JSONL file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps({
                    "timestamp": e.timestamp,
                    "caller": e.caller,
                    "method": e.method,
                    "args": e.args,
                    "input_count": e.input_count,
                    "output_count": e.output_count,
                    "output_sample": e.output_sample,
                }) + "\n")

    def clear(self) -> None:
        self.entries.clear()


# ---------------------------------------------------------------------------
# KnowledgeGraph -- entry point
# ---------------------------------------------------------------------------


class KnowledgeGraph:
    """Entry point for fluent graph queries.

    Agents use this. Backend (NetworkX) is never exposed.
    """

    def __init__(
        self,
        backend: NetworkXBackend,
        vectors: VectorStore | None = None,
        embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
    ) -> None:
        self._backend = backend
        self._vectors = vectors
        self._embed_fn = embed_fn
        self._trace = TraceContext()

    def enable_trace(self, caller: str = "") -> None:
        """Start logging KG operations."""
        self._trace.enabled = True
        self._trace.caller = caller

    def disable_trace(self) -> None:
        """Stop logging KG operations."""
        self._trace.enabled = False

    def save_trace(self, path: Path) -> None:
        """Append trace entries to JSONL file and clear buffer."""
        self._trace.save(path)
        self._trace.clear()

    # ---- Entry points returning QueryBuilder ----

    def source(self, source_id: str) -> QueryBuilder:
        """Start from a single source."""
        ids = {source_id} if self._backend.has_node(source_id) else set()
        return QueryBuilder(self, ids, SOURCE)

    def author(self, author_key: str) -> QueryBuilder:
        """Start from a single author."""
        ids = {author_key} if self._backend.has_node(author_key) else set()
        return QueryBuilder(self, ids, AUTHOR)

    def sources(self, **filters: object) -> QueryBuilder:
        """All sources, optionally filtered."""
        ids = self._backend.nodes_of_type(SOURCE)
        qb = QueryBuilder(self, ids, SOURCE)
        if filters:
            qb = qb.where(**filters)
        return qb

    def authors(self, **filters: object) -> QueryBuilder:
        """All authors, optionally filtered."""
        ids = self._backend.nodes_of_type(AUTHOR)
        qb = QueryBuilder(self, ids, AUTHOR)
        if filters:
            qb = qb.where(**filters)
        return qb

    def chunks(self, *, include_boilerplate: bool = False, **filters: object) -> QueryBuilder:
        """All chunks, optionally filtered.

        Boilerplate-flagged chunks are excluded by default; pass
        ``include_boilerplate=True`` to opt back in.
        """
        ids = self._backend.nodes_of_type(CHUNK)
        qb = QueryBuilder(self, ids, CHUNK, include_boilerplate=include_boilerplate)
        if filters:
            qb = qb.where(**filters)
        return qb

    # ---- Convenience: direct vector search (no graph traversal) ----

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Search all chunks by vector similarity."""
        return self.chunks().search(query, top_k=top_k)

    # ---- Corpus stats ----

    def corpus_stats(self) -> dict:
        """Pre-computed corpus metrics."""
        backend = self._backend
        n_sources = len(backend.nodes_of_type(SOURCE))
        n_authors = len(backend.nodes_of_type(AUTHOR))
        n_chunks = len(backend.nodes_of_type(CHUNK))
        n_sections = len(backend.nodes_of_type(SECTION))
        n_figures = len(backend.nodes_of_type(FIGURE))
        n_equations = len(backend.nodes_of_type(EQUATION))
        return {
            "sources": n_sources,
            "authors": n_authors,
            "chunks": n_chunks,
            "sections": n_sections,
            "figures": n_figures,
            "equations": n_equations,
            "edges": self._backend.G.number_of_edges(),
        }
