"""Community detection helpers over dense weighted adjacency matrices.

Thin wrappers around ``networkx.community`` so the metric code stays
independent of any particular backend. Inputs are symmetric weighted
numpy adjacency matrices; outputs are ``list[set[int]]`` partitions and
scalar modularity values.
"""

import networkx as nx
import numpy as np
from networkx.algorithms import community as nx_community


def _build_graph(adj: np.ndarray) -> nx.Graph:
    """Build an undirected weighted ``nx.Graph`` from a symmetric dense
    adjacency matrix.

    Only upper-triangular nonzero entries are added as edges. Isolated
    nodes are still present in the graph (node ids 0..n-1) so every
    index is accounted for in the returned partition.
    """
    n = int(adj.shape[0])
    g: nx.Graph = nx.Graph()
    g.add_nodes_from(range(n))
    if n < 2:
        return g
    iu, ju = np.triu_indices(n, k=1)
    w = adj[iu, ju]
    nz = w > 0
    for i, j, weight in zip(iu[nz].tolist(), ju[nz].tolist(), w[nz].tolist(), strict=True):
        g.add_edge(int(i), int(j), weight=float(weight))
    return g


def louvain_communities(adj: np.ndarray, *, seed: int = 0) -> list[set[int]]:
    """Run Louvain community detection on a symmetric weighted adjacency.

    Deterministic for a fixed ``seed``. Returns a list of disjoint sets
    of node indices whose union covers ``range(adj.shape[0])``.
    """
    n = int(adj.shape[0])
    if n == 0:
        return []
    if n == 1:
        return [{0}]
    g = _build_graph(adj)
    if g.number_of_edges() == 0:
        return [{i} for i in range(n)]
    parts = nx_community.louvain_communities(g, weight="weight", seed=seed)
    return [set(int(x) for x in c) for c in parts]


def modularity(adj: np.ndarray, communities: list[set[int]]) -> float:
    """Weighted modularity of ``communities`` under ``adj``.

    Thin wrapper over ``networkx.community.modularity``. Returns 0.0 for
    empty or edgeless graphs.
    """
    n = int(adj.shape[0])
    if n == 0 or not communities:
        return 0.0
    g = _build_graph(adj)
    if g.number_of_edges() == 0:
        return 0.0
    return float(nx_community.modularity(g, [set(c) for c in communities], weight="weight"))
