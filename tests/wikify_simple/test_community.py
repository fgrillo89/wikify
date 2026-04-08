"""Tests for eval.community (Louvain wrapper)."""

from __future__ import annotations

import numpy as np

from wikify_simple.eval.community import louvain_communities, modularity


def _two_block_graph() -> np.ndarray:
    """10+10 node graph: dense within each block (w=1.0), sparse across
    (w=0.05). Symmetric, zero diagonal."""
    n = 20
    w = np.full((n, n), 0.05, dtype=float)
    for i in range(10):
        for j in range(10):
            w[i, j] = 1.0
            w[i + 10, j + 10] = 1.0
    np.fill_diagonal(w, 0.0)
    return w


def test_louvain_two_blocks_returns_two_communities():
    w = _two_block_graph()
    comms = louvain_communities(w, seed=0)
    assert len(comms) == 2
    # each community is exactly one 10-node block
    sizes = sorted(len(c) for c in comms)
    assert sizes == [10, 10]
    # the two blocks are the natural partition {0..9} and {10..19}
    block_a = {i for i in range(10)}
    assert block_a in comms or {i for i in range(10, 20)} in comms


def test_modularity_two_blocks_above_threshold():
    w = _two_block_graph()
    comms = louvain_communities(w, seed=0)
    q = modularity(w, comms)
    assert q > 0.3


def test_louvain_empty_graph():
    assert louvain_communities(np.zeros((0, 0))) == []


def test_louvain_single_node():
    assert louvain_communities(np.zeros((1, 1))) == [{0}]


def test_louvain_edgeless_graph():
    comms = louvain_communities(np.zeros((4, 4)))
    assert comms == [{0}, {1}, {2}, {3}]
    assert modularity(np.zeros((4, 4)), comms) == 0.0
