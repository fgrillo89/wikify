"""Corpus profiling: graph metrics, importance scores, community detection.

Computes the analytics an orchestrator needs to make smart decisions about
what to extract and write. All metrics are derived from the CorpusGraph
materialized at ingest time -- no additional model calls needed.

Usage:
    profile = build_corpus_profile(corpus)
    profile.doc_pagerank["[2020 Liu] ..."]  # -> 0.087
    profile.doc_roles["[2020 Liu] ..."]     # -> "core"
    profile.communities                      # -> {0: ["doc1", "doc2"], 1: [...]}
    profile.hub_concepts[:10]               # -> top 10 concept chunks by centrality
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..paths import CorpusPaths
from ..store.corpus import all_chunks, list_documents, read_graph


@dataclass
class CorpusProfile:
    """Precomputed analytics over the corpus graph."""

    n_docs: int = 0
    n_chunks: int = 0
    topics: list[str] = field(default_factory=list)

    # Document-level metrics
    doc_pagerank: dict[str, float] = field(default_factory=dict)
    doc_degree: dict[str, int] = field(default_factory=dict)
    doc_in_degree: dict[str, int] = field(default_factory=dict)  # citation authority
    doc_roles: dict[str, str] = field(default_factory=dict)  # core | bridge | peripheral
    doc_importance: dict[str, float] = field(default_factory=dict)  # blended score

    # Community structure
    communities: dict[int, list[str]] = field(default_factory=dict)  # community_id -> doc_ids
    doc_community: dict[str, int] = field(default_factory=dict)  # doc_id -> community_id

    # Chunk-level metrics (sparse, only for high-value chunks)
    hub_chunks: list[str] = field(default_factory=list)  # chunk_ids sorted by centrality

    def top_docs(self, n: int = 10) -> list[tuple[str, float]]:
        """Top-n documents by importance score."""
        return sorted(self.doc_importance.items(), key=lambda x: -x[1])[:n]

    def docs_in_community(self, community_id: int) -> list[str]:
        """Document IDs in a given community."""
        return self.communities.get(community_id, [])

    def summary(self) -> dict:
        """Compact summary for the orchestrator prompt."""
        return {
            "n_docs": self.n_docs,
            "n_chunks": self.n_chunks,
            "n_communities": len(self.communities),
            "topics": self.topics[:20],
            "top_docs": [
                {"id": did, "importance": round(imp, 3), "role": self.doc_roles.get(did, "?")}
                for did, imp in self.top_docs(10)
            ],
            "community_sizes": {k: len(v) for k, v in sorted(self.communities.items())},
        }


def build_corpus_profile(corpus: CorpusPaths) -> CorpusProfile:
    """Build a full corpus profile from the materialized graph and metadata."""
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    graph = read_graph(corpus)
    topics = _load_topics(corpus)

    profile = CorpusProfile(
        n_docs=len(docs),
        n_chunks=len(chunks),
        topics=topics,
    )

    doc_ids = [d.id for d in docs]
    if not doc_ids:
        return profile

    # Build adjacency from graph edges
    cites_edges = graph.edges.get("cites", []) if graph else []
    similar_edges = graph.edges.get("doc_similar", []) if graph else []
    chunk_sim_edges = graph.edges.get("similar_strong", []) if graph else []

    # PageRank on citation graph
    profile.doc_pagerank = _pagerank(doc_ids, cites_edges)

    # Degree centrality (undirected: cites + doc_similar)
    all_doc_edges = cites_edges + similar_edges
    profile.doc_degree = _degree(doc_ids, all_doc_edges)

    # In-degree (citation authority)
    profile.doc_in_degree = _in_degree(doc_ids, cites_edges)

    # Source diversity: how many distinct docs cite each doc
    source_div = _source_diversity(doc_ids, cites_edges)

    # Blended importance: 0.5*PR + 0.3*degree_norm + 0.2*source_div_norm
    profile.doc_importance = _blended_importance(
        doc_ids, profile.doc_pagerank, profile.doc_degree, source_div
    )

    # Node roles: core / bridge / peripheral
    profile.doc_roles = _classify_roles(
        doc_ids, profile.doc_importance, profile.doc_degree, cites_edges
    )

    # Community detection on doc similarity graph
    profile.communities, profile.doc_community = _detect_communities(
        doc_ids, all_doc_edges
    )

    # Hub chunks: top chunks by kNN degree
    profile.hub_chunks = _hub_chunks(chunks, chunk_sim_edges)

    return profile


def _pagerank(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Power-iteration PageRank. No networkx dependency."""
    n = len(node_ids)
    if n == 0:
        return {}

    idx = {nid: i for i, nid in enumerate(node_ids)}
    adj: dict[int, list[int]] = defaultdict(list)
    out_degree: dict[int, int] = Counter()

    for src, dst in edges:
        si, di = idx.get(src), idx.get(dst)
        if si is not None and di is not None:
            adj[si].append(di)
            out_degree[si] += 1

    rank = [1.0 / n] * n
    teleport = (1.0 - damping) / n

    for _ in range(max_iter):
        new_rank = [teleport] * n
        dangling_mass = sum(rank[i] for i in range(n) if out_degree[i] == 0)
        dangling_contrib = damping * dangling_mass / n

        for i in range(n):
            if out_degree[i] == 0:
                continue
            share = damping * rank[i] / out_degree[i]
            for j in adj[i]:
                new_rank[j] += share

        for i in range(n):
            new_rank[i] += dangling_contrib

        # Check convergence
        diff = sum(abs(new_rank[i] - rank[i]) for i in range(n))
        rank = new_rank
        if diff < tol:
            break

    return {node_ids[i]: rank[i] for i in range(n)}


def _degree(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Undirected degree count."""
    deg: dict[str, int] = {nid: 0 for nid in node_ids}
    node_set = set(node_ids)
    for src, dst in edges:
        if src in node_set:
            deg[src] = deg.get(src, 0) + 1
        if dst in node_set:
            deg[dst] = deg.get(dst, 0) + 1
    return deg


def _in_degree(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Directed in-degree (citation authority)."""
    ind: dict[str, int] = {nid: 0 for nid in node_ids}
    node_set = set(node_ids)
    for _, dst in edges:
        if dst in node_set:
            ind[dst] = ind.get(dst, 0) + 1
    return ind


def _source_diversity(
    node_ids: list[str], cites_edges: list[tuple[str, str]]
) -> dict[str, float]:
    """Fraction of corpus docs that cite each node."""
    n = len(node_ids)
    if n <= 1:
        return {nid: 0.0 for nid in node_ids}
    node_set = set(node_ids)
    citers: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for src, dst in cites_edges:
        if dst in node_set and src in node_set:
            citers[dst].add(src)
    return {nid: len(citers[nid]) / (n - 1) for nid in node_ids}


def _blended_importance(
    node_ids: list[str],
    pagerank: dict[str, float],
    degree: dict[str, int],
    source_div: dict[str, float],
) -> dict[str, float]:
    """0.5*PR_norm + 0.3*degree_norm + 0.2*source_diversity."""
    if not node_ids:
        return {}
    max_pr = max(pagerank.values()) or 1.0
    max_deg = max(degree.values()) or 1
    return {
        nid: (
            0.5 * (pagerank.get(nid, 0) / max_pr)
            + 0.3 * (degree.get(nid, 0) / max_deg)
            + 0.2 * source_div.get(nid, 0)
        )
        for nid in node_ids
    }


def _classify_roles(
    node_ids: list[str],
    importance: dict[str, float],
    degree: dict[str, int],
    cites_edges: list[tuple[str, str]],
) -> dict[str, str]:
    """Classify nodes as core / bridge / peripheral."""
    if not node_ids:
        return {}

    # Betweenness heuristic: nodes that connect different clusters
    # (simplified: nodes cited by AND citing other nodes)
    node_set = set(node_ids)
    cites_out: dict[str, set[str]] = defaultdict(set)
    cites_in: dict[str, set[str]] = defaultdict(set)
    for src, dst in cites_edges:
        if src in node_set and dst in node_set:
            cites_out[src].add(dst)
            cites_in[dst].add(src)

    median_deg = sorted(degree.values())[len(degree) // 2] if degree else 0
    roles: dict[str, str] = {}

    for nid in node_ids:
        imp = importance.get(nid, 0)
        deg = degree.get(nid, 0)
        # Bridge: cites AND is cited by different nodes (connector)
        is_bridge = len(cites_out.get(nid, set())) >= 1 and len(cites_in.get(nid, set())) >= 1
        if imp > 0.5 and deg > median_deg:
            roles[nid] = "core"
        elif is_bridge and imp > 0.2:
            roles[nid] = "bridge"
        else:
            roles[nid] = "peripheral"

    return roles


def _detect_communities(
    node_ids: list[str], edges: list[tuple[str, str]]
) -> tuple[dict[int, list[str]], dict[str, int]]:
    """Simple community detection via connected components + greedy modularity.

    Falls back to connected components if the graph is too sparse for
    meaningful community structure.
    """
    if not node_ids:
        return {}, {}

    # Build adjacency
    node_set = set(node_ids)
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for src, dst in edges:
        if src in node_set and dst in node_set:
            adj[src].add(dst)
            adj[dst].add(src)

    # Connected components via BFS
    visited: set[str] = set()
    components: list[list[str]] = []
    for nid in node_ids:
        if nid in visited:
            continue
        component: list[str] = []
        queue = [nid]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    # Sort communities by size (largest first)
    components.sort(key=len, reverse=True)

    communities: dict[int, list[str]] = {}
    doc_community: dict[str, int] = {}
    for i, comp in enumerate(components):
        communities[i] = comp
        for nid in comp:
            doc_community[nid] = i

    return communities, doc_community


def _hub_chunks(chunks, sim_edges: list[tuple[str, str]]) -> list[str]:
    """Top chunks by similarity-graph degree (most connected = most central)."""
    chunk_ids = {c.id for c in chunks}
    deg: Counter[str] = Counter()
    for src, dst in sim_edges:
        if src in chunk_ids:
            deg[src] += 1
        if dst in chunk_ids:
            deg[dst] += 1
    return [cid for cid, _ in deg.most_common(50)]


def _load_topics(corpus: CorpusPaths) -> list[str]:
    """Load corpus-wide topics."""
    topics_path = corpus.topics_path
    if not topics_path.exists():
        return []
    data = json.loads(topics_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("topics", []))
    return []
