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

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

_MARKER_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")

if TYPE_CHECKING:
    import networkx as nx

    from ..store.vectors import VectorStore


# ---------------------------------------------------------------------------
# Citation marker parsing (standalone, no class needed)
# ---------------------------------------------------------------------------


def parse_citation_markers(text: str) -> list[int]:
    """Parse citation markers like [1-3], [4,5] from text.

    Returns sorted unique ordinals. Standalone function -- does not need
    a RefLookup or any corpus state.
    """
    nums: list[int] = []
    for m in _MARKER_RE.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    nums.extend(range(int(a.strip()), int(b.strip()) + 1))
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
    """Phase 1 backend: NetworkX graph + inverted dict indexes."""

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
            elif kind == "FIGURE_NEAR_CHUNK":
                self._figures_near_chunk.setdefault(v, []).append(u)

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
    """

    __slots__ = ("_kg", "_ids", "_type")

    def __init__(
        self,
        kg: KnowledgeGraph,
        node_ids: set[str],
        node_type: str | None = None,
    ) -> None:
        self._kg = kg
        self._ids = frozenset(node_ids)
        self._type = node_type

    # ---- Traversal (returns new QueryBuilder) ----

    def cited_by(self) -> QueryBuilder:
        """Sources that cite these sources."""
        result: set[str] = set()
        idx = self._kg._backend._cited_by
        for sid in self._ids:
            result |= idx.get(sid, set())
        return QueryBuilder(self._kg, result, SOURCE)

    def references(self, ords: list[int] | None = None) -> QueryBuilder:
        """Sources cited by these sources. Optionally filter by ordinal."""
        if ords is not None:
            # Resolve specific ordinals via ord_refs
            result: set[str] = set()
            ord_idx = self._kg._backend._ord_refs
            for sid in self._ids:
                om = ord_idx.get(sid, {})
                for n in ords:
                    target = om.get(n)
                    if target:
                        result.add(target)
            return QueryBuilder(self._kg, result, SOURCE)
        result = set()
        idx = self._kg._backend._references
        for sid in self._ids:
            result |= idx.get(sid, set())
        return QueryBuilder(self._kg, result, SOURCE)

    def neighborhood(self, hops: int = 1) -> QueryBuilder:
        """N-hop undirected graph neighbors."""
        result: set[str] = set()
        for nid in self._ids:
            result |= self._kg._backend.neighbors(nid, hops)
        return QueryBuilder(self._kg, result, None)

    def authors(self) -> QueryBuilder:
        """Authors of these sources."""
        result: set[str] = set()
        idx = self._kg._backend._authors_of
        for sid in self._ids:
            result |= idx.get(sid, set())
        return QueryBuilder(self._kg, result, AUTHOR)

    def sources(self) -> QueryBuilder:
        """Sources by these authors."""
        result: set[str] = set()
        idx = self._kg._backend._sources_of
        for aid in self._ids:
            result |= idx.get(aid, set())
        return QueryBuilder(self._kg, result, SOURCE)

    def coauthors(self) -> QueryBuilder:
        """Co-authors of these authors."""
        result: set[str] = set()
        idx = self._kg._backend._coauthors
        for aid in self._ids:
            result |= idx.get(aid, set())
        result -= self._ids  # exclude self
        return QueryBuilder(self._kg, result, AUTHOR)

    def sections(self, type: str | None = None) -> QueryBuilder:
        """Sections of these sources. Optionally filter by section type."""
        result: set[str] = set()
        idx = self._kg._backend._sections_of
        for sid in self._ids:
            result.update(idx.get(sid, []))
        if type is not None:
            backend = self._kg._backend
            result = {
                sid for sid in result
                if backend.G.nodes[sid].get("section_type") == type
            }
        return QueryBuilder(self._kg, result, SECTION)

    def chunks(self) -> QueryBuilder:
        """Chunks of these sources or sections."""
        result: set[str] = set()
        backend = self._kg._backend
        if self._type == SECTION:
            idx = backend._chunks_of_section
            for sid in self._ids:
                result.update(idx.get(sid, []))
        else:
            # For sources, get chunks directly
            idx = backend._chunks_of_source
            for sid in self._ids:
                result.update(idx.get(sid, []))
            # Also handle if current set contains sections mixed in
            sec_idx = backend._chunks_of_section
            for sid in self._ids:
                if sid in sec_idx:
                    result.update(sec_idx[sid])
        return QueryBuilder(self._kg, result, CHUNK)

    def figures(self) -> QueryBuilder:
        """Figures of these sources."""
        result: set[str] = set()
        idx = self._kg._backend._figures_of
        for sid in self._ids:
            result.update(idx.get(sid, []))
        return QueryBuilder(self._kg, result, FIGURE)

    def equations(self) -> QueryBuilder:
        """Equations of these sources."""
        result: set[str] = set()
        idx = self._kg._backend._equations_of
        for sid in self._ids:
            result.update(idx.get(sid, []))
        return QueryBuilder(self._kg, result, EQUATION)

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
        return QueryBuilder(self._kg, result, self._type)

    def of_type(self, kind: str) -> QueryBuilder:
        """Filter by node type."""
        backend = self._kg._backend
        result = {
            nid for nid in self._ids
            if nid in backend.G and backend.G.nodes[nid].get("type") == kind
        }
        return QueryBuilder(self._kg, result, kind)

    def since(self, year: int) -> QueryBuilder:
        """Filter sources by year >= N."""
        backend = self._kg._backend
        result = {
            nid for nid in self._ids
            if nid in backend.G and (backend.G.nodes[nid].get("year") or 0) >= year
        }
        return QueryBuilder(self._kg, result, self._type)

    def top(self, n: int, by: str) -> QueryBuilder:
        """Top N by a metric (pagerank, year, citation_count, h_index)."""
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
        result = {nid for _, nid in scored[:n]}
        return QueryBuilder(self._kg, result, self._type)

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
        return results

    def _resolve_chunk_scope(self) -> set[str]:
        """Resolve the current set to chunk IDs for vector search."""
        if self._type == CHUNK:
            return set(self._ids)
        # If current set is sources/sections/etc, get their chunks
        backend = self._kg._backend
        chunk_ids: set[str] = set()
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
                chunk_ids.update(backend._figures_near_chunk.get(nid, []))
            elif ntype == EQUATION:
                chunk_ids.update(backend._equations_in_chunk.get(nid, []))
        return chunk_ids

    # ---- Terminals (execute and return) ----

    def collect(self) -> list[dict]:
        """Materialize all nodes as dicts."""
        backend = self._kg._backend
        return [
            backend.node(nid) for nid in sorted(self._ids)
            if backend.has_node(nid)
        ]

    def ids(self) -> list[str]:
        """Return just the node IDs."""
        return sorted(self._ids)

    def count(self) -> int:
        """Count matches."""
        return len(self._ids)

    def first(self) -> dict | None:
        """First result or None."""
        if not self._ids:
            return None
        nid = min(self._ids)
        backend = self._kg._backend
        return backend.node(nid) if backend.has_node(nid) else None

    def exists(self) -> bool:
        """Any matches?"""
        return len(self._ids) > 0

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

    def chunks(self, **filters: object) -> QueryBuilder:
        """All chunks, optionally filtered."""
        ids = self._backend.nodes_of_type(CHUNK)
        qb = QueryBuilder(self, ids, CHUNK)
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
