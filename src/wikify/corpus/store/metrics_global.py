"""Global graph metrics over named graph views.

Each `GraphView` projects a slice of `graph_edges` (one or more edge
kinds, one or more node types, directed/undirected). Metrics that
require a graph traversal — PageRank, betweenness, h-index, degree
centrality — are computed by NetworkX over the projected subgraph and
written into `node_metrics` keyed by `(graph_name, node_type, node_id,
metric)`.

NetworkX is used as an algorithm library only. The graph itself never
lives outside the projection: the data lands back in SQLite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

import networkx as nx

from .metrics import (
    CITATION_COUNT,
    VIEW_AUTHOR_COAUTHOR,
    VIEW_CORPUS_CITATION,
    _now,
    _upsert_view,
    refresh_citation_count,
)

PAGERANK = "pagerank"
H_INDEX = "h_index"
DEGREE_CENTRALITY = "degree_centrality"
BETWEENNESS = "betweenness"


# ---------------------------------------------------------------------------
# Graph view registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphView:
    """One named projection of `graph_edges`.

    name           : registry key (matches `graph_views.graph_name`).
    description    : one-line human description.
    node_types     : node types kept in the projection.
    edge_kinds     : `graph_edges.kind` values kept in the projection.
    directed       : project as nx.DiGraph (True) or nx.Graph (False).
    seed_table     : SQL fragment that yields candidate node ids; used
                     to ensure isolated nodes still receive a row.
    """

    name: str
    description: str
    node_types: tuple[str, ...]
    edge_kinds: tuple[str, ...]
    directed: bool
    seed_sql: str = ""
    metrics: tuple[str, ...] = field(default=("pagerank", "degree_centrality"))


VIEWS: dict[str, GraphView] = {
    VIEW_CORPUS_CITATION: GraphView(
        name=VIEW_CORPUS_CITATION,
        description="document nodes; references edges; directed",
        node_types=("document",),
        edge_kinds=("references",),
        directed=True,
        seed_sql="SELECT doc_id FROM documents",
        metrics=("pagerank", "degree_centrality"),
    ),
    VIEW_AUTHOR_COAUTHOR: GraphView(
        name=VIEW_AUTHOR_COAUTHOR,
        description="author nodes; coauthor edges; undirected",
        node_types=("author",),
        edge_kinds=("coauthor",),
        directed=False,
        seed_sql="SELECT author_id FROM authors",
        metrics=("degree_centrality",),
    ),
    "chunk_citation": GraphView(
        name="chunk_citation",
        description="chunk -> bib_entry cites; bib_entry -> document resolved_to; directed",
        node_types=("chunk", "bib_entry", "document"),
        edge_kinds=("cites", "resolved_to"),
        directed=True,
        seed_sql="",
        metrics=("degree_centrality",),
    ),
}


def list_views() -> list[GraphView]:
    return list(VIEWS.values())


def get_view(name: str) -> GraphView:
    if name not in VIEWS:
        raise ValueError(f"unknown view {name!r}; expected one of {sorted(VIEWS)}")
    return VIEWS[name]


# ---------------------------------------------------------------------------
# Projection: pull a view into NetworkX
# ---------------------------------------------------------------------------


def project_view(con: sqlite3.Connection, view: GraphView) -> nx.Graph:
    """Build a NetworkX graph from `graph_edges` for *view*.

    Isolated nodes from `seed_sql` are added too so per-node metrics
    (degree, pagerank) are present even when the node has no edges.
    """
    g: nx.Graph = nx.DiGraph() if view.directed else nx.Graph()
    if view.seed_sql:
        for r in con.execute(view.seed_sql):
            g.add_node(r[0])
    placeholders = ",".join("?" * len(view.edge_kinds))
    sql = (
        "SELECT src_id, dst_id, weight FROM graph_edges "
        f"WHERE kind IN ({placeholders})"
    )
    params: list[object] = list(view.edge_kinds)
    if view.node_types:
        type_ph = ",".join("?" * len(view.node_types))
        sql += (
            f" AND src_type IN ({type_ph}) AND dst_type IN ({type_ph})"
        )
        params.extend(view.node_types)
        params.extend(view.node_types)
    for r in con.execute(sql, params):
        weight = float(r[2]) if r[2] is not None else 1.0
        g.add_edge(r[0], r[1], weight=weight)
    return g


# ---------------------------------------------------------------------------
# Metric writers
# ---------------------------------------------------------------------------


def _set_status(con: sqlite3.Connection, projection: str, scope_id: str, status: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO projection_status(projection, scope_type, scope_id, "
        "status, updated_at) VALUES (?, 'view', ?, ?, ?)",
        (projection, scope_id, status, datetime.now(UTC).isoformat()),
    )


def _write_node_metric(
    con: sqlite3.Connection,
    *,
    graph_name: str,
    node_type: str,
    metric: str,
    values: dict[str, float],
) -> None:
    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE graph_name=? AND metric=?",
        (graph_name, metric),
    )
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (graph_name, node_type, nid, metric, round(float(v), 8), now)
            for nid, v in values.items()
        ],
    )


def refresh_pagerank(
    con: sqlite3.Connection,
    *,
    view: str | GraphView = VIEW_CORPUS_CITATION,
    alpha: float = 0.85,
) -> None:
    """Compute PageRank over *view* and store under that view's name."""
    v = view if isinstance(view, GraphView) else get_view(view)
    _upsert_view(
        con,
        graph_name=v.name,
        description=v.description,
        node_types=list(v.node_types),
        edge_kinds=list(v.edge_kinds),
        directed=v.directed,
    )
    g = project_view(con, v)
    scores = nx.pagerank(g, alpha=alpha) if g.nodes else {}
    node_type = v.node_types[0] if v.node_types else "node"
    _write_node_metric(
        con, graph_name=v.name, node_type=node_type,
        metric=PAGERANK, values=scores,
    )
    _set_status(con, projection="metrics", scope_id=v.name, status="fresh")


def refresh_degree_centrality(
    con: sqlite3.Connection,
    *,
    view: str | GraphView,
) -> None:
    """Normalized degree centrality per node over *view*."""
    v = view if isinstance(view, GraphView) else get_view(view)
    _upsert_view(
        con,
        graph_name=v.name,
        description=v.description,
        node_types=list(v.node_types),
        edge_kinds=list(v.edge_kinds),
        directed=v.directed,
    )
    g = project_view(con, v)
    scores = nx.degree_centrality(g) if g.nodes else {}
    node_type = v.node_types[0] if v.node_types else "node"
    _write_node_metric(
        con, graph_name=v.name, node_type=node_type,
        metric=DEGREE_CENTRALITY, values=scores,
    )
    _set_status(con, projection="metrics", scope_id=v.name, status="fresh")


def refresh_h_index(con: sqlite3.Connection) -> None:
    """h-index per author over their authored documents' citation_count.

    Citation counts are refreshed first so a stale projection cannot leak
    into the h-index numbers.
    """
    refresh_citation_count(con)
    cite_by_doc = dict(con.execute(
        "SELECT node_id, value FROM node_metrics "
        "WHERE graph_name=? AND metric=?",
        (VIEW_CORPUS_CITATION, CITATION_COUNT),
    ))
    by_author: dict[str, list[float]] = {}
    for r in con.execute("SELECT author_id, doc_id FROM document_authors"):
        by_author.setdefault(r[0], []).append(float(cite_by_doc.get(r[1], 0.0)))

    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE node_type='author' AND metric=?",
        (H_INDEX,),
    )
    rows = []
    for author_id, counts in by_author.items():
        ordered = sorted(counts, reverse=True)
        h = 0
        for i, c in enumerate(ordered, start=1):
            if c >= i:
                h = i
            else:
                break
        rows.append((
            "author_h_index", "author", author_id, H_INDEX, float(h), now,
        ))
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    _set_status(con, projection="metrics", scope_id="author_h_index", status="fresh")


def refresh_view(con: sqlite3.Connection, view: str | GraphView) -> list[str]:
    """Run every metric registered for *view*. Returns the metric names written."""
    v = view if isinstance(view, GraphView) else get_view(view)
    written: list[str] = []
    for metric in v.metrics:
        if metric == PAGERANK:
            refresh_pagerank(con, view=v)
            written.append(PAGERANK)
        elif metric == DEGREE_CENTRALITY:
            refresh_degree_centrality(con, view=v)
            written.append(DEGREE_CENTRALITY)
    return written


# ---------------------------------------------------------------------------
# Status / freshness helpers
# ---------------------------------------------------------------------------


def view_status(con: sqlite3.Connection, view: str) -> dict | None:
    row = con.execute(
        "SELECT projection, scope_id, status, updated_at FROM projection_status "
        "WHERE projection='metrics' AND scope_id=?",
        (view,),
    ).fetchone()
    return dict(row) if row else None


def is_stale(con: sqlite3.Connection, view: str) -> bool:
    s = view_status(con, view)
    return not s or s["status"] != "fresh"
