"""Community detection and graph topology metrics.

Pure-graph operations: given a ``DiGraph`` from ``build.py``, run Louvain
community detection and compute structural metrics (modularity,
inter-community edge ratio, bridge density, community gini, spectral gap).
"""

from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx
import numpy as np

logger = logging.getLogger(__name__)


def detect_communities(graph: nx.DiGraph) -> dict[str, int]:
    """Louvain community detection over the undirected projection.

    Returns ``{concept_id -> community_index}`` with index 0 reserved for
    the largest community.
    """

    if graph.number_of_nodes() == 0:
        return {}

    undirected = graph.to_undirected()
    if undirected.number_of_edges() == 0:
        return {n: 0 for n in graph.nodes()}

    community_sets: list[set] = list(
        nx.community.louvain_communities(undirected, weight="weight", seed=42)
    )
    community_sets.sort(key=len, reverse=True)

    membership: dict[str, int] = {}
    for idx, members in enumerate(community_sets):
        for node in members:
            membership[node] = idx

    logger.info(
        "detect_communities: %d communities from %d nodes",
        len(community_sets),
        graph.number_of_nodes(),
    )
    return membership


def compute_modularity(graph: nx.DiGraph, communities: dict[str, int]) -> float:
    """Modularity score of a community partition (roughly [0, 1])."""

    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        return 0.0

    index_to_members: dict[int, set[str]] = defaultdict(set)
    for node, idx in communities.items():
        index_to_members[idx].add(node)
    community_sets: list[set[str]] = list(index_to_members.values())
    if not community_sets:
        return 0.0

    score: float = nx.community.modularity(graph.to_undirected(), community_sets)
    logger.debug("compute_modularity: %.4f from %d communities", score, len(community_sets))
    return score


def compute_inter_community_edge_ratio(
    graph: nx.DiGraph,
    communities: dict[str, int],
) -> float:
    """Fraction of edges that cross community boundaries."""

    total_edges = graph.number_of_edges()
    if total_edges == 0:
        return 0.0

    inter = sum(
        1 for src, tgt in graph.edges() if communities.get(src) != communities.get(tgt)
    )
    return inter / total_edges


def compute_bridge_density(roles: dict[str, str]) -> float:
    """Proportion of nodes classified as ``bridge``."""

    if not roles:
        return 0.0
    bridge_count = sum(1 for role in roles.values() if role == "bridge")
    return bridge_count / len(roles)


def compute_community_gini(communities: dict[str, int]) -> float:
    """Gini coefficient of community sizes (0 = uniform, 1 = single dominant)."""

    if not communities:
        return 0.0

    size_counter: dict[int, int] = defaultdict(int)
    for idx in communities.values():
        size_counter[idx] += 1

    n_communities = len(size_counter)
    if n_communities <= 1:
        return 0.0

    sizes = sorted(size_counter.values())
    n = len(sizes)
    total = sum(sizes)
    if total == 0:
        return 0.0

    weighted_sum = sum((i + 1) * x for i, x in enumerate(sizes))
    gini = (2 * weighted_sum) / (n * total) - (n + 1) / n
    return max(0.0, min(1.0, gini))


def compute_spectral_gap(graph: nx.DiGraph) -> float:
    """Spectral gap (Fiedler value) of the undirected projection."""

    if graph.number_of_nodes() < 2:
        return 0.0
    try:
        eigenvalues = np.sort(nx.laplacian_spectrum(graph.to_undirected()))
        gap = float(eigenvalues[1] - eigenvalues[0])
        return max(0.0, gap)
    except Exception:
        logger.debug("compute_spectral_gap: failed; returning 0.0")
        return 0.0


__all__ = [
    "compute_bridge_density",
    "compute_community_gini",
    "compute_inter_community_edge_ratio",
    "compute_modularity",
    "compute_spectral_gap",
    "detect_communities",
]
