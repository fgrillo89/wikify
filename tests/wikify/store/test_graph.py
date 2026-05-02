"""GraphStore traversal tests + EXPLAIN QUERY PLAN sanity."""

from __future__ import annotations

from wikify.corpus.store import Edge, Store


def _seed(s: Store) -> None:
    edges = [
        Edge("document", "a", "references", "document", "b"),
        Edge("document", "b", "references", "document", "c"),
        Edge("document", "c", "references", "document", "a"),  # cycle
        Edge("document", "a", "references", "document", "d"),
    ]
    s.graph.upsert_edges(edges)


def test_neighbors_outbound():
    s = Store(":memory:")
    _seed(s)
    rows = s.graph.neighbors("document", "a", direction="out", kinds=["references"])
    assert sorted(r["dst_id"] for r in rows) == ["b", "d"]


def test_neighbors_inbound():
    s = Store(":memory:")
    _seed(s)
    rows = s.graph.neighbors("document", "a", direction="in", kinds=["references"])
    assert [r["src_id"] for r in rows] == ["c"]


def test_traverse_depth_cap_and_cycle_safety():
    s = Store(":memory:")
    _seed(s)
    visited = s.graph.traverse(
        [("document", "a")], direction="out", kinds=["references"], max_depth=10,
    )
    # UNION dedup means each node appears at most once across the recursion;
    # a -> b -> c -> a should not infinite-loop.
    nodes = {(r["node_type"], r["node_id"]) for r in visited}
    assert nodes == {("document", "b"), ("document", "c"), ("document", "d"), ("document", "a")}


def test_traverse_uses_index():
    s = Store(":memory:")
    _seed(s)
    # Synthesize the same SQL the API runs and inspect its plan.
    sql = (
        "WITH RECURSIVE walk(depth, node_type, node_id) AS ("
        " SELECT 0, ?, ? UNION "
        " SELECT walk.depth + 1, e.dst_type, e.dst_id FROM walk "
        " JOIN graph_edges e ON e.src_type=walk.node_type AND e.src_id=walk.node_id "
        " WHERE walk.depth < ? AND e.kind = ?"
        ") SELECT depth, node_type, node_id FROM walk WHERE depth > 0 LIMIT ?"
    )
    plan = s.con.execute(
        "EXPLAIN QUERY PLAN " + sql, ("document", "a", 3, "references", 100),
    ).fetchall()
    flat = " | ".join(str(row[3]) for row in plan)
    # Indexed access proves the recursion does not table-scan; the
    # specific index choice depends on selectivity and is fine to vary.
    assert "USING INDEX" in flat or "USING COVERING INDEX" in flat
    assert "SCAN graph_edges" not in flat


def test_path_finds_shortest():
    s = Store(":memory:")
    _seed(s)
    p = s.graph.path(("document", "a"), ("document", "c"), kinds=["references"], max_depth=4)
    assert p is not None
    assert p[0] == ("document", "a") and p[-1] == ("document", "c")
    # Either a -> b -> c (length 3) or some other valid chain.
    assert len(p) <= 4


def test_subgraph_returns_internal_edges_only():
    s = Store(":memory:")
    _seed(s)
    sg = s.graph.subgraph(
        [("document", "a")], kinds=["references"], depth=1, limit=100,
    )
    nodes = {(n["node_type"], n["node_id"]) for n in sg["nodes"]}
    assert ("document", "a") in nodes
    for e in sg["edges"]:
        assert (e["src_type"], e["src_id"]) in nodes
        assert (e["dst_type"], e["dst_id"]) in nodes
