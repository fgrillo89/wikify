"""Tests for topology metric functions in wikify.wiki.graph.build."""

from __future__ import annotations

import networkx as nx
import pytest

from wikify.wiki.graph.build import (
    compute_bridge_density,
    compute_community_gini,
    compute_inter_community_edge_ratio,
    compute_modularity,
    compute_spectral_gap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_cluster_graph() -> tuple[nx.DiGraph, dict[str, int]]:
    """Return a graph with two well-separated cliques and the community mapping."""
    g = nx.DiGraph()
    # Cluster 0: a, b, c — fully connected
    for u, v in [("a", "b"), ("a", "c"), ("b", "c")]:
        g.add_edge(u, v, weight=1.0)
        g.add_edge(v, u, weight=1.0)
    # Cluster 1: d, e, f — fully connected
    for u, v in [("d", "e"), ("d", "f"), ("e", "f")]:
        g.add_edge(u, v, weight=1.0)
        g.add_edge(v, u, weight=1.0)

    communities = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}
    return g, communities


# ---------------------------------------------------------------------------
# compute_modularity
# ---------------------------------------------------------------------------


class TestComputeModularity:
    def test_two_clear_clusters(self):
        """Two well-separated clusters should yield modularity > 0.3."""
        graph, communities = _two_cluster_graph()
        q = compute_modularity(graph, communities)
        assert q > 0.3, f"Expected modularity > 0.3, got {q}"

    def test_empty_graph(self):
        """Empty graph returns 0.0."""
        graph = nx.DiGraph()
        communities: dict[str, int] = {}
        result = compute_modularity(graph, communities)
        assert result == 0.0

    def test_graph_no_edges(self):
        """Graph with nodes but no edges returns 0.0 (no edges to partition)."""
        graph = nx.DiGraph()
        graph.add_node("a")
        graph.add_node("b")
        communities = {"a": 0, "b": 1}
        result = compute_modularity(graph, communities)
        assert result == 0.0

    def test_single_community(self):
        """All nodes in one community: modularity should be 0.0 or very close."""
        graph, _ = _two_cluster_graph()
        # Force all nodes into one community
        communities = {n: 0 for n in graph.nodes()}
        q = compute_modularity(graph, communities)
        assert abs(q) < 1e-6, f"Single community should give modularity ~0, got {q}"

    def test_returns_float(self):
        """Return type is always float."""
        graph, communities = _two_cluster_graph()
        result = compute_modularity(graph, communities)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_inter_community_edge_ratio
# ---------------------------------------------------------------------------


class TestComputeInterCommunityEdgeRatio:
    def test_no_cross_edges(self):
        """Two isolated cliques with no cross-edges -> ratio == 0.0."""
        graph, communities = _two_cluster_graph()
        ratio = compute_inter_community_edge_ratio(graph, communities)
        assert ratio == 0.0

    def test_all_cross_edges(self):
        """All edges cross community boundaries -> ratio == 1.0."""
        graph = nx.DiGraph()
        # Nodes in different communities connected to each other only
        graph.add_edge("a", "d", weight=1.0)
        graph.add_edge("d", "a", weight=1.0)
        graph.add_edge("b", "e", weight=1.0)
        graph.add_edge("e", "b", weight=1.0)
        communities = {"a": 0, "b": 0, "d": 1, "e": 1}
        ratio = compute_inter_community_edge_ratio(graph, communities)
        assert ratio == 1.0

    def test_mixed_edges(self):
        """Some cross, some within: ratio is strictly between 0 and 1."""
        # Cluster 0: a-b (intra) + a-d (cross)
        graph = nx.DiGraph()
        graph.add_edge("a", "b", weight=1.0)  # intra
        graph.add_edge("b", "a", weight=1.0)  # intra (reverse)
        graph.add_edge("a", "d", weight=1.0)  # cross
        graph.add_edge("d", "a", weight=1.0)  # cross (reverse)
        communities = {"a": 0, "b": 0, "d": 1}

        ratio = compute_inter_community_edge_ratio(graph, communities)
        assert 0.0 < ratio < 1.0, f"Expected mixed ratio in (0, 1), got {ratio}"

    def test_no_edges_returns_zero(self):
        """Graph with no edges returns 0.0."""
        graph = nx.DiGraph()
        graph.add_node("a")
        graph.add_node("b")
        communities = {"a": 0, "b": 1}
        result = compute_inter_community_edge_ratio(graph, communities)
        assert result == 0.0

    def test_returns_float(self):
        """Return type is always float."""
        graph, communities = _two_cluster_graph()
        result = compute_inter_community_edge_ratio(graph, communities)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_bridge_density
# ---------------------------------------------------------------------------


class TestComputeBridgeDensity:
    def test_no_bridges(self):
        """All core/peripheral -> density == 0.0."""
        roles = {"a": "core", "b": "core", "c": "peripheral"}
        density = compute_bridge_density(roles)
        assert density == 0.0

    def test_some_bridges(self):
        """1 bridge out of 3 nodes -> density == 1/3."""
        roles = {"a": "bridge", "b": "core", "c": "peripheral"}
        density = compute_bridge_density(roles)
        assert abs(density - 1 / 3) < 1e-9

    def test_all_bridges(self):
        """All bridge nodes -> density == 1.0."""
        roles = {"a": "bridge", "b": "bridge", "c": "bridge"}
        density = compute_bridge_density(roles)
        assert density == 1.0

    def test_empty_roles(self):
        """Empty roles dict -> 0.0."""
        result = compute_bridge_density({})
        assert result == 0.0

    def test_returns_float(self):
        """Return type is always float."""
        roles = {"a": "bridge", "b": "core"}
        result = compute_bridge_density(roles)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_community_gini
# ---------------------------------------------------------------------------


class TestComputeCommunityGini:
    def test_equal_sizes(self):
        """Two communities of equal size -> Gini near 0.0."""
        # 5 nodes in community 0, 5 in community 1
        communities = {f"c0_{i}": 0 for i in range(5)}
        communities.update({f"c1_{i}": 1 for i in range(5)})
        gini = compute_community_gini(communities)
        assert gini < 0.1, f"Equal-size communities should give Gini near 0, got {gini}"

    def test_unequal_sizes(self):
        """9 nodes in cluster 0, 1 in cluster 1 -> Gini > 0.3."""
        communities = {f"n{i}": 0 for i in range(9)}
        communities["n9"] = 1
        gini = compute_community_gini(communities)
        assert gini > 0.3, f"Highly unequal communities should give Gini > 0.3, got {gini}"

    def test_single_community(self):
        """Single community -> Gini == 0.0."""
        communities = {"a": 0, "b": 0, "c": 0}
        result = compute_community_gini(communities)
        assert result == 0.0

    def test_empty_communities(self):
        """Empty mapping -> 0.0."""
        result = compute_community_gini({})
        assert result == 0.0

    def test_gini_clamped_to_unit_interval(self):
        """Result must always be in [0, 1]."""
        communities = {f"n{i}": i for i in range(10)}  # 10 communities, 1 node each
        result = compute_community_gini(communities)
        assert 0.0 <= result <= 1.0

    def test_returns_float(self):
        """Return type is always float."""
        communities = {"a": 0, "b": 1}
        result = compute_community_gini(communities)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_spectral_gap
# ---------------------------------------------------------------------------


class TestComputeSpectralGap:
    def test_connected_graph(self):
        """A connected graph should have spectral gap > 0."""
        graph = nx.DiGraph()
        for u, v in [("a", "b"), ("b", "c"), ("c", "a")]:
            graph.add_edge(u, v, weight=1.0)
            graph.add_edge(v, u, weight=1.0)

        gap = compute_spectral_gap(graph)
        assert gap > 0.0, f"Connected graph should have spectral gap > 0, got {gap}"

    def test_disconnected_graph(self):
        """Two disconnected components -> spectral gap == 0.0 (lambda_2 = 0)."""
        graph = nx.DiGraph()
        # Component 1
        graph.add_edge("a", "b", weight=1.0)
        graph.add_edge("b", "a", weight=1.0)
        # Component 2 (disconnected)
        graph.add_edge("c", "d", weight=1.0)
        graph.add_edge("d", "c", weight=1.0)

        gap = compute_spectral_gap(graph)
        assert gap == 0.0, f"Disconnected graph should have spectral gap == 0, got {gap}"

    def test_empty_graph(self):
        """Empty graph -> 0.0."""
        graph = nx.DiGraph()
        result = compute_spectral_gap(graph)
        assert result == 0.0

    def test_single_node(self):
        """Single-node graph -> 0.0 (fewer than 2 nodes)."""
        graph = nx.DiGraph()
        graph.add_node("solo")
        result = compute_spectral_gap(graph)
        assert result == 0.0

    def test_returns_non_negative(self):
        """Spectral gap must always be >= 0."""
        graph, _ = _two_cluster_graph()
        result = compute_spectral_gap(graph)
        assert result >= 0.0

    def test_returns_float(self):
        """Return type is always float."""
        graph, _ = _two_cluster_graph()
        result = compute_spectral_gap(graph)
        assert isinstance(result, float)
