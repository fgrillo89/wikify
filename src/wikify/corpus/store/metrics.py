"""Graph-projection metrics over `graph_edges`.

Phase 5 covers cheap incremental metrics that recompute in O(edges) per
update — citation_count, coauthor_count, in/out degree. PageRank and
h-index are deferred to Phase 6 (`metrics_global.py`).

Metrics are stored in `node_metrics` keyed by (graph_name, node_type,
node_id, metric); skill code reads them via `get_node_metric`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Iterable

VIEW_CORPUS_CITATION = "corpus_citation"
VIEW_AUTHOR_COAUTHOR = "author_coauthor"

CITATION_COUNT = "citation_count"
COAUTHOR_COUNT = "coauthor_count"
IN_DEGREE = "in_degree"
OUT_DEGREE = "out_degree"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _upsert_view(
    con: sqlite3.Connection,
    *,
    graph_name: str,
    description: str,
    node_types: Iterable[str],
    edge_kinds: Iterable[str],
    directed: bool,
    weighted: bool = False,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO graph_views(graph_name, description, "
        "node_types_json, edge_kinds_json, directed, weighted, params_json, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            graph_name, description,
            json.dumps(list(node_types)), json.dumps(list(edge_kinds)),
            int(directed), int(weighted), json.dumps({}), _now(),
        ),
    )


def refresh_citation_count(con: sqlite3.Connection) -> None:
    """citation_count = number of incoming `references` edges per document."""
    _upsert_view(
        con,
        graph_name=VIEW_CORPUS_CITATION,
        description="document nodes; references edges; directed",
        node_types=["document"],
        edge_kinds=["references"],
        directed=True,
    )
    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE graph_name=? AND metric=?",
        (VIEW_CORPUS_CITATION, CITATION_COUNT),
    )
    rows = con.execute(
        "SELECT documents.doc_id AS doc_id, COALESCE(c.cnt, 0) AS cnt "
        "FROM documents LEFT JOIN ("
        "  SELECT dst_id AS doc_id, COUNT(*) AS cnt FROM graph_edges "
        "  WHERE kind='references' AND dst_type='document' GROUP BY dst_id"
        ") c ON c.doc_id = documents.doc_id",
    ).fetchall()
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES (?, 'document', ?, ?, ?, ?)",
        [(VIEW_CORPUS_CITATION, r["doc_id"], CITATION_COUNT, float(r["cnt"]), now) for r in rows],
    )


def refresh_coauthor_count(con: sqlite3.Connection) -> None:
    """coauthor_count = degree of each author in the undirected coauthor graph."""
    _upsert_view(
        con,
        graph_name=VIEW_AUTHOR_COAUTHOR,
        description="author nodes; coauthor edges; undirected",
        node_types=["author"],
        edge_kinds=["coauthor"],
        directed=False,
    )
    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE graph_name=? AND metric=?",
        (VIEW_AUTHOR_COAUTHOR, COAUTHOR_COUNT),
    )
    # Edge stored once with src_id<dst_id; each edge contributes to both endpoints.
    rows = con.execute(
        "SELECT a.author_id AS aid, COALESCE(c.cnt, 0) AS cnt "
        "FROM authors a LEFT JOIN ("
        "  SELECT author_id, SUM(cnt) AS cnt FROM ("
        "    SELECT src_id AS author_id, COUNT(*) AS cnt FROM graph_edges "
        "      WHERE kind='coauthor' GROUP BY src_id "
        "    UNION ALL "
        "    SELECT dst_id AS author_id, COUNT(*) AS cnt FROM graph_edges "
        "      WHERE kind='coauthor' GROUP BY dst_id"
        "  ) GROUP BY author_id"
        ") c ON c.author_id = a.author_id",
    ).fetchall()
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES (?, 'author', ?, ?, ?, ?)",
        [(VIEW_AUTHOR_COAUTHOR, r["aid"], COAUTHOR_COUNT, float(r["cnt"]), now) for r in rows],
    )


def refresh_degree_metrics(con: sqlite3.Connection) -> None:
    """in_degree / out_degree per (node_type, node_id) across all edges."""
    _upsert_view(
        con,
        graph_name="all_edges",
        description="every node; every edge; directed",
        node_types=["document", "chunk", "author", "bib_entry", "asset", "wiki_page"],
        edge_kinds=["*"],
        directed=True,
    )
    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE graph_name='all_edges' AND metric IN (?, ?)",
        (IN_DEGREE, OUT_DEGREE),
    )
    out_rows = con.execute(
        "SELECT src_type, src_id, COUNT(*) FROM graph_edges GROUP BY src_type, src_id",
    ).fetchall()
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES ('all_edges', ?, ?, ?, ?, ?)",
        [(r[0], r[1], OUT_DEGREE, float(r[2]), now) for r in out_rows],
    )
    in_rows = con.execute(
        "SELECT dst_type, dst_id, COUNT(*) FROM graph_edges GROUP BY dst_type, dst_id",
    ).fetchall()
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES ('all_edges', ?, ?, ?, ?, ?)",
        [(r[0], r[1], IN_DEGREE, float(r[2]), now) for r in in_rows],
    )


def refresh_cheap_metrics(con: sqlite3.Connection) -> None:
    """Run all O(edges) metrics: citation_count, coauthor_count, degree."""
    refresh_citation_count(con)
    refresh_coauthor_count(con)
    refresh_degree_metrics(con)


def get_node_metric(
    con: sqlite3.Connection,
    *,
    graph_name: str,
    node_type: str,
    node_id: str,
    metric: str,
) -> float | None:
    row = con.execute(
        "SELECT value FROM node_metrics "
        "WHERE graph_name=? AND node_type=? AND node_id=? AND metric=?",
        (graph_name, node_type, node_id, metric),
    ).fetchone()
    return float(row[0]) if row else None


def list_node_metric(
    con: sqlite3.Connection,
    *,
    graph_name: str,
    node_type: str,
    metric: str,
    top_k: int = 100,
    desc: bool = True,
) -> list[tuple[str, float]]:
    order = "DESC" if desc else "ASC"
    rows = con.execute(
        f"SELECT node_id, value FROM node_metrics "
        f"WHERE graph_name=? AND node_type=? AND metric=? "
        f"ORDER BY value {order}, node_id LIMIT ?",
        (graph_name, node_type, metric, top_k),
    ).fetchall()
    return [(r[0], float(r[1])) for r in rows]
