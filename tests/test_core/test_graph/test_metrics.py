"""Tests for wikify.core.graph.metrics."""

from __future__ import annotations

import networkx as nx

from wikify.core.graph.metrics import GraphMetrics, compute_metrics

# ── GraphMetrics dataclass ────────────────────────────────────────────────────


def test_graph_metrics_defaults():
    m = GraphMetrics()
    assert m.pagerank == {}
    assert m.degree_centrality == {}
    assert m.betweenness_centrality == {}
    assert m.hub_papers == []
    assert m.bridge_papers == []
    assert m.peripheral_papers == []


def test_paper_role_hub():
    m = GraphMetrics(hub_papers=["p1"], bridge_papers=[], peripheral_papers=[])
    assert "hub" in m.paper_role("p1")


def test_paper_role_bridge():
    m = GraphMetrics(hub_papers=[], bridge_papers=["p2"], peripheral_papers=[])
    assert "bridge" in m.paper_role("p2")


def test_paper_role_peripheral():
    m = GraphMetrics(hub_papers=[], bridge_papers=[], peripheral_papers=["p3"])
    assert "frontier" in m.paper_role("p3")


def test_paper_role_multiple():
    # A paper can be both hub and bridge (though compute_metrics excludes bridges that are hubs)
    m = GraphMetrics(hub_papers=["p1"], bridge_papers=["p1"], peripheral_papers=[])
    role = m.paper_role("p1")
    assert "hub" in role
    assert "bridge" in role


def test_paper_role_standard():
    m = GraphMetrics(hub_papers=["p1"], bridge_papers=["p2"], peripheral_papers=["p3"])
    assert m.paper_role("p4") == "standard"


def test_summary_for_llm_includes_sections():
    m = GraphMetrics(
        pagerank={"p1": 0.4, "p2": 0.1},
        betweenness_centrality={"p3": 0.5},
        hub_papers=["p1"],
        bridge_papers=["p3"],
        peripheral_papers=["p2"],
    )
    names = {"p1": "Hub Paper", "p2": "Peripheral Paper", "p3": "Bridge Paper"}
    text = m.summary_for_llm(names)
    assert "Hub Paper" in text
    assert "Bridge Paper" in text
    assert "Peripheral Paper" in text
    assert "PageRank" in text
    assert "betweenness" in text


def test_summary_for_llm_empty():
    m = GraphMetrics()
    text = m.summary_for_llm({})
    assert "Graph Analysis" in text


def test_summary_for_llm_unknown_id_fallback():
    m = GraphMetrics(
        pagerank={"abcdef123456789": 0.3},
        hub_papers=["abcdef123456789"],
    )
    text = m.summary_for_llm({})  # no names provided
    # falls back to first 12 chars of ID
    assert "abcdef123456" in text


# ── compute_metrics with known graph ─────────────────────────────────────────


def _build_star_graph() -> nx.DiGraph:
    """Hub-and-spoke: p0 is highly connected, p5 is isolated."""
    g = nx.DiGraph()
    nodes = [f"p{i}" for i in range(6)]
    for n in nodes:
        g.add_node(n)
    # p0 ← p1..p4 (p0 is cited by many → high PageRank)
    for i in range(1, 5):
        g.add_edge(f"p{i}", "p0", weight=1.0)
    # p5 has no edges → peripheral
    return g


def _build_bridge_graph() -> nx.DiGraph:
    """Two clusters connected by a single bridge node."""
    g = nx.DiGraph()
    nodes = [f"p{i}" for i in range(10)]
    for n in nodes:
        g.add_node(n)
    # Cluster A: p0-p3 fully connected
    for i in range(4):
        for j in range(4):
            if i != j:
                g.add_edge(f"p{i}", f"p{j}", weight=1.0)
    # Cluster B: p6-p9 fully connected
    for i in range(6, 10):
        for j in range(6, 10):
            if i != j:
                g.add_edge(f"p{i}", f"p{j}", weight=1.0)
    # Bridge: p5 connects both clusters
    g.add_edge("p5", "p0", weight=1.0)
    g.add_edge("p5", "p6", weight=1.0)
    g.add_edge("p0", "p5", weight=1.0)
    g.add_edge("p6", "p5", weight=1.0)
    return g


def test_compute_metrics_empty_graph():
    m = compute_metrics(nx.DiGraph())
    assert m.pagerank == {}
    assert m.hub_papers == []


def test_compute_metrics_single_node():
    g = nx.DiGraph()
    g.add_node("only")
    m = compute_metrics(g)
    assert "only" in m.pagerank
    assert len(m.hub_papers) == 1
    assert m.hub_papers[0] == "only"


def test_compute_metrics_hub_identified():
    g = _build_star_graph()
    m = compute_metrics(g)
    # p0 receives citations from p1..p4 → highest PageRank → should be a hub
    assert "p0" in m.hub_papers


def test_compute_metrics_peripheral_identified():
    g = _build_star_graph()
    m = compute_metrics(g)
    # p5 has no edges → lowest degree centrality → peripheral
    assert "p5" in m.peripheral_papers


def test_compute_metrics_all_nodes_have_pagerank():
    g = _build_star_graph()
    m = compute_metrics(g)
    for node in g.nodes():
        assert node in m.pagerank


def test_compute_metrics_bridge_excluded_from_hubs():
    """Bridge papers should not appear in hub_papers."""
    g = _build_bridge_graph()
    m = compute_metrics(g)
    for bp in m.bridge_papers:
        assert bp not in m.hub_papers


def test_compute_metrics_counts_within_bounds():
    g = _build_star_graph()
    m = compute_metrics(g)
    n = g.number_of_nodes()
    max_count = max(1, n // 5)
    assert len(m.hub_papers) <= max_count
    assert len(m.peripheral_papers) <= max_count


def test_compute_metrics_pagerank_sums_to_one():
    g = _build_star_graph()
    m = compute_metrics(g)
    total = sum(m.pagerank.values())
    assert abs(total - 1.0) < 1e-6


def test_compute_metrics_degree_centrality_range():
    g = _build_star_graph()
    m = compute_metrics(g)
    for v in m.degree_centrality.values():
        assert 0.0 <= v <= 1.0
