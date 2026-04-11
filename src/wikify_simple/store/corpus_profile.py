"""Corpus profiling: graph metrics, importance, community detection.

Computes the analytics an orchestrator needs to make smart decisions
about what to extract and write. All metrics derive from the CorpusGraph
materialized at ingest time -- no model calls needed.

Design principles:
  - **Document-type agnostic**: importance comes from PageRank on the
    unified document graph (cites + doc_similar + coupling), not from
    citations alone. Works for papers, blog posts, manuals, reports.
  - **Minimal arbitrariness**: one importance score (PageRank), one
    community algorithm (Louvain), one bridge metric (betweenness).
    No blended weights to tune.
  - **Cheap**: runs in milliseconds on hundreds of documents.

Usage:
    profile = build_corpus_profile(corpus)
    profile.doc_importance["[2020 Liu] ..."]  # -> 0.087
    profile.doc_roles["[2020 Liu] ..."]       # -> "core"
    profile.communities                        # -> {0: ["doc1", ...], ...}
"""

import json
from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from ..paths import CorpusPaths
from ..store.corpus import all_chunks, list_documents, read_graph


@dataclass
class CorpusProfile:
    """Precomputed analytics over the corpus graph."""

    n_docs: int = 0
    n_chunks: int = 0
    topics: list[str] = field(default_factory=list)

    # Document-level metrics (all from unified graph PageRank)
    doc_importance: dict[str, float] = field(default_factory=dict)
    doc_betweenness: dict[str, float] = field(default_factory=dict)
    doc_roles: dict[str, str] = field(default_factory=dict)

    # Community structure (Louvain)
    communities: dict[int, list[str]] = field(default_factory=dict)
    doc_community: dict[str, int] = field(default_factory=dict)
    modularity: float = 0.0

    # Chunk-level: most central chunks by similarity-graph degree
    hub_chunks: list[str] = field(default_factory=list)

    def top_docs(self, n: int = 10) -> list[tuple[str, float]]:
        """Top-n documents by importance."""
        return sorted(self.doc_importance.items(), key=lambda x: -x[1])[:n]

    def docs_in_community(self, community_id: int) -> list[str]:
        return self.communities.get(community_id, [])

    def summary(self) -> dict:
        """Compact summary for orchestrator prompts."""
        return {
            "n_docs": self.n_docs,
            "n_chunks": self.n_chunks,
            "n_communities": len(self.communities),
            "modularity": round(self.modularity, 3),
            "topics": self.topics[:20],
            "top_docs": [
                {
                    "id": did,
                    "importance": round(imp, 3),
                    "role": self.doc_roles.get(did, "?"),
                    "community": self.doc_community.get(did, -1),
                }
                for did, imp in self.top_docs(10)
            ],
            "community_sizes": {k: len(v) for k, v in sorted(self.communities.items())},
        }


def build_corpus_profile(corpus: CorpusPaths) -> CorpusProfile:
    """Build a corpus profile from the materialized graph."""
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

    # Build unified document graph from ALL edge types.
    # This makes importance document-type agnostic: papers get credit
    # from citations, blog posts from semantic similarity, etc.
    g = _build_unified_doc_graph(doc_ids, graph)

    if g.number_of_nodes() == 0:
        return profile

    # PageRank on the unified graph = the single importance score.
    profile.doc_importance = dict(nx.pagerank(g, weight="weight"))

    # Betweenness centrality for bridge detection.
    profile.doc_betweenness = dict(nx.betweenness_centrality(g, weight="weight"))

    # Node roles from importance + betweenness.
    profile.doc_roles = _classify_roles(doc_ids, profile.doc_importance, profile.doc_betweenness)

    # Louvain community detection.
    profile.communities, profile.doc_community, profile.modularity = _louvain_communities(
        g, doc_ids
    )

    # Hub chunks by similarity-graph degree.
    chunk_sim_edges = graph.edges.get("similar_strong", []) if graph else []
    profile.hub_chunks = _hub_chunks(chunks, chunk_sim_edges)

    return profile


def _build_unified_doc_graph(doc_ids: list[str], graph) -> nx.Graph:
    """Build an undirected weighted graph from all document-level edges.

    Edge types used (all contribute equally):
      - cites: doc -> doc (from parsed references)
      - doc_similar: doc <-> doc (embedding cosine >= threshold)
      - cites_same: doc <-> doc (bibliographic coupling — shared refs)

    Each edge type adds weight 1.0. If a pair has both a citation and
    a similarity edge, the combined weight is 2.0. This naturally
    boosts documents that are both semantically similar and citation-linked.
    """
    g = nx.Graph()
    g.add_nodes_from(doc_ids)

    if graph is None:
        return g

    node_set = set(doc_ids)
    edge_types = ["cites", "doc_similar", "cites_same"]

    for etype in edge_types:
        for src, dst in graph.edges.get(etype, []):
            if src not in node_set or dst not in node_set:
                continue
            if src == dst:
                continue
            if g.has_edge(src, dst):
                g[src][dst]["weight"] += 1.0
            else:
                g.add_edge(src, dst, weight=1.0)

    return g


def _classify_roles(
    doc_ids: list[str],
    importance: dict[str, float],
    betweenness: dict[str, float],
) -> dict[str, str]:
    """Classify documents as core / bridge / peripheral.

    - core: top quartile by importance
    - bridge: top quartile by betweenness (but not core)
    - peripheral: everything else
    """
    if not doc_ids:
        return {}

    n = len(doc_ids)
    imp_sorted = sorted(doc_ids, key=lambda d: importance.get(d, 0), reverse=True)
    bet_sorted = sorted(doc_ids, key=lambda d: betweenness.get(d, 0), reverse=True)

    q1 = max(n // 4, 1)
    core_set = set(imp_sorted[:q1])
    bridge_set = set(bet_sorted[:q1]) - core_set

    roles: dict[str, str] = {}
    for did in doc_ids:
        if did in core_set:
            roles[did] = "core"
        elif did in bridge_set:
            roles[did] = "bridge"
        else:
            roles[did] = "peripheral"
    return roles


def _louvain_communities(
    g: nx.Graph, doc_ids: list[str]
) -> tuple[dict[int, list[str]], dict[str, int], float]:
    """Louvain community detection on the unified doc graph."""
    if g.number_of_edges() == 0:
        # No edges: each doc is its own community
        communities = {i: [did] for i, did in enumerate(doc_ids)}
        doc_community = {did: i for i, did in enumerate(doc_ids)}
        return communities, doc_community, 0.0

    parts = list(nx.community.louvain_communities(g, weight="weight", seed=0))
    # Sort by size, largest first
    parts.sort(key=len, reverse=True)

    communities: dict[int, list[str]] = {}
    doc_community: dict[str, int] = {}
    for i, part in enumerate(parts):
        members = sorted(part)
        communities[i] = members
        for did in members:
            doc_community[did] = i

    modularity = float(nx.community.modularity(g, parts, weight="weight"))
    return communities, doc_community, modularity


def _hub_chunks(chunks, sim_edges: list[tuple[str, str]]) -> list[str]:
    """Top chunks by similarity-graph degree."""
    chunk_ids = {c.id for c in chunks}
    deg: Counter[str] = Counter()
    for src, dst in sim_edges:
        if src in chunk_ids:
            deg[src] += 1
        if dst in chunk_ids:
            deg[dst] += 1
    return [cid for cid, _ in deg.most_common(50)]


def _load_topics(corpus: CorpusPaths) -> list[str]:
    topics_path = corpus.topics_path
    if not topics_path.exists():
        return []
    data = json.loads(topics_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list(data.get("topics", []))
    return []
