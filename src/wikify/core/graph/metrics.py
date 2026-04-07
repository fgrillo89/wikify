"""Graph-based metrics for guiding LLM literature traversal.

Computes centrality, PageRank, and peripheral node detection from
the citation and similarity graphs. Used to prioritize key papers
and identify frontier topics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import Paper


@dataclass
class GraphMetrics:
    """Metrics computed for each paper in the corpus graph."""

    # Paper ID -> metric value
    pagerank: dict[str, float] = field(default_factory=dict)  # citations-only PageRank
    pagerank_mixed: dict[str, float] = field(default_factory=dict)  # mixed graph PageRank
    degree_centrality: dict[str, float] = field(default_factory=dict)
    betweenness_centrality: dict[str, float] = field(default_factory=dict)
    # Classified roles
    hub_papers: list[str] = field(default_factory=list)  # Top citation PageRank
    bridge_papers: list[str] = field(default_factory=list)  # Top betweenness
    peripheral_papers: list[str] = field(default_factory=list)  # Low degree, frontier topics

    def paper_role(self, paper_id: str) -> str:
        """Return a human-readable role for a paper."""
        roles = []
        if paper_id in self.hub_papers:
            roles.append("hub (highly connected)")
        if paper_id in self.bridge_papers:
            roles.append("bridge (connects clusters)")
        if paper_id in self.peripheral_papers:
            roles.append("frontier (peripheral)")
        return ", ".join(roles) if roles else "standard"

    def summary_for_llm(self, id_to_name: dict[str, str]) -> str:
        """Format metrics as context for LLM prompts."""
        lines = ["## Graph Analysis\n"]

        if self.hub_papers:
            lines.append("**Key hub papers** (most connected, start here):")
            for pid in self.hub_papers:
                name = id_to_name.get(pid, pid[:12])
                pr = self.pagerank.get(pid, 0)
                lines.append(f"  - {name} (PageRank: {pr:.3f})")

        if self.bridge_papers:
            lines.append("\n**Bridge papers** (connect different research areas):")
            for pid in self.bridge_papers:
                name = id_to_name.get(pid, pid[:12])
                bc = self.betweenness_centrality.get(pid, 0)
                lines.append(f"  - {name} (betweenness: {bc:.3f})")

        if self.peripheral_papers:
            lines.append("\n**Frontier papers** (peripheral, may cover emerging topics):")
            for pid in self.peripheral_papers:
                name = id_to_name.get(pid, pid[:12])
                lines.append(f"  - {name}")

        return "\n".join(lines)


def build_citation_only_graph() -> nx.DiGraph:
    """Build a directed graph from citation links ONLY.

    No similarity or coupling edges. Used for pure citation-based
    PageRank as an orthogonal signal to embedding-based metrics.
    """
    from wikify.ingest.extract.cite_match import build_citation_graph
    from wikify.core.store.models import Citation

    graph = nx.DiGraph()

    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    for p in papers:
        graph.add_node(p.id, title=p.title, year=p.year)

    citations_by_paper: dict[str, list[str]] = {}
    with get_session() as session:
        for p in papers:
            cites = session.exec(select(Citation).where(Citation.paper_id == p.id)).all()
            if cites:
                citations_by_paper[p.id] = [c.raw_text for c in cites]

    citation_graph = build_citation_graph(papers, citations_by_paper)
    for citing_id, cited_ids in citation_graph.items():
        for cited_id in cited_ids:
            graph.add_edge(citing_id, cited_id, weight=1.0)

    return graph


def build_corpus_graph() -> nx.DiGraph:
    """Build a directed graph from citation links + undirected similarity edges.

    Nodes are papers. Edges come from:
    1. Direct citations (directed: A cites B)
    2. k-NN similarity (undirected, lower weight)
    3. Bibliographic coupling (undirected)
    """
    from wikify.core.store.embeddings import get_all_similar
    from wikify.ingest.vault.coupler import compute_coupling

    graph = nx.DiGraph()

    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    paper_ids = [p.id for p in papers]
    for p in papers:
        authors = p.parsed_authors
        graph.add_node(p.id, title=p.title, year=p.year, authors=authors)

    # 1. Citation edges (directed)
    from wikify.ingest.extract.cite_match import build_citation_graph
    from wikify.core.store.models import Citation

    citations_by_paper: dict[str, list[str]] = {}
    with get_session() as session:
        for p in papers:
            cites = session.exec(select(Citation).where(Citation.paper_id == p.id)).all()
            if cites:
                citations_by_paper[p.id] = [c.raw_text for c in cites]

    citation_graph = build_citation_graph(papers, citations_by_paper)
    for citing_id, cited_ids in citation_graph.items():
        for cited_id in cited_ids:
            graph.add_edge(citing_id, cited_id, type="cites", weight=1.0)

    # 2. Similarity edges (undirected, added as bidirectional)
    similar_map = get_all_similar(paper_ids, n_results=5)
    for pid, similar_ids in similar_map.items():
        for sid in similar_ids:
            if not graph.has_edge(pid, sid):
                graph.add_edge(pid, sid, type="similar", weight=0.3)
            if not graph.has_edge(sid, pid):
                graph.add_edge(sid, pid, type="similar", weight=0.3)

    # 3. Coupling edges (undirected)
    coupling_map = compute_coupling(paper_ids)
    for pid, coupled_ids in coupling_map.items():
        for cid in coupled_ids:
            if not graph.has_edge(pid, cid):
                graph.add_edge(pid, cid, type="coupling", weight=0.5)
            if not graph.has_edge(cid, pid):
                graph.add_edge(cid, pid, type="coupling", weight=0.5)

    return graph


def compute_metrics(corpus_graph: nx.DiGraph | None = None) -> GraphMetrics:
    """Compute all graph metrics. Builds graph if not provided.

    PageRank is computed on CITATIONS ONLY (directed A-cites-B edges),
    giving a pure academic authority signal orthogonal to embedding-based
    metrics. The mixed graph is used for degree and betweenness centrality.
    """
    if corpus_graph is None:
        corpus_graph = build_corpus_graph()

    n = corpus_graph.number_of_nodes()
    if n == 0:
        return GraphMetrics()

    # Citation-only PageRank (pure authority signal)
    # Extract citation-only subgraph from the corpus graph
    cite_graph = nx.DiGraph()
    cite_graph.add_nodes_from(corpus_graph.nodes(data=True))
    for u, v, data in corpus_graph.edges(data=True):
        if data.get("type") == "cites":
            cite_graph.add_edge(u, v, weight=data.get("weight", 1.0))

    if cite_graph.number_of_edges() > 0:
        pagerank_cite = nx.pagerank(cite_graph, weight="weight")
    else:
        # Fallback to mixed if no citation edges
        pagerank_cite = nx.pagerank(corpus_graph, weight="weight")

    # Mixed-graph PageRank (for reference / backward compatibility)
    pagerank_mixed = nx.pagerank(corpus_graph, weight="weight")

    # Degree centrality (on undirected view for overall connectivity)
    undirected = corpus_graph.to_undirected()
    degree_cent = nx.degree_centrality(undirected)

    # Betweenness centrality (identifies bridge papers)
    betweenness = nx.betweenness_centrality(undirected, weight="weight")

    # Classify papers
    # Hubs: top 20% by CITATION PageRank (pure authority)
    hub_threshold = max(1, n // 5)
    sorted_pr = sorted(pagerank_cite.items(), key=lambda x: x[1], reverse=True)
    hub_papers = [pid for pid, _ in sorted_pr[:hub_threshold]]

    # Bridges: top 20% by betweenness (excluding hubs to avoid overlap)
    bridge_threshold = max(1, n // 5)
    sorted_bc = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)
    bridge_papers = [pid for pid, _ in sorted_bc[:bridge_threshold] if pid not in hub_papers]

    # Peripheral: bottom 20% by degree centrality
    peripheral_threshold = max(1, n // 5)
    sorted_dc = sorted(degree_cent.items(), key=lambda x: x[1])
    peripheral_papers = [pid for pid, _ in sorted_dc[:peripheral_threshold]]

    return GraphMetrics(
        pagerank=pagerank_cite,
        pagerank_mixed=pagerank_mixed,
        degree_centrality=degree_cent,
        betweenness_centrality=betweenness,
        hub_papers=hub_papers,
        bridge_papers=bridge_papers,
        peripheral_papers=peripheral_papers,
    )
