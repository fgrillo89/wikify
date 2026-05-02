"""Global graph metrics: PageRank and h-index.

Phase 6 metrics — gated behind explicit
`wikify corpus metrics refresh --view <name>` because they touch the
whole graph and we don't want them on every ingest. Stale reads carry
a stale flag based on `projection_status`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import networkx as nx

from .metrics import (
    CITATION_COUNT,
    VIEW_CORPUS_CITATION,
    _now,
    _upsert_view,
    refresh_citation_count,
)

PAGERANK = "pagerank"
H_INDEX = "h_index"


def _set_status(con: sqlite3.Connection, projection: str, scope_id: str, status: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO projection_status(projection, scope_type, scope_id, "
        "status, updated_at) VALUES (?, 'view', ?, ?, ?)",
        (projection, scope_id, status, datetime.now(UTC).isoformat()),
    )


def refresh_pagerank(con: sqlite3.Connection, *, alpha: float = 0.85) -> None:
    """Compute PageRank over the document `references` subgraph.

    Builds a NetworkX DiGraph on the fly from `graph_edges` rows; nx
    handles the CSR + power iteration internally. Writes one
    `pagerank` value per document into node_metrics; updates
    `projection_status` to fresh on success.
    """
    _upsert_view(
        con,
        graph_name=VIEW_CORPUS_CITATION,
        description="document nodes; references edges; directed",
        node_types=["document"],
        edge_kinds=["references"],
        directed=True,
    )
    g = nx.DiGraph()
    for r in con.execute("SELECT doc_id FROM documents"):
        g.add_node(r[0])
    for r in con.execute(
        "SELECT src_id, dst_id FROM graph_edges "
        "WHERE kind='references' AND src_type='document' AND dst_type='document'",
    ):
        g.add_edge(r[0], r[1])
    if not g.nodes:
        scores: dict[str, float] = {}
    else:
        scores = nx.pagerank(g, alpha=alpha)

    now = _now()
    con.execute(
        "DELETE FROM node_metrics WHERE graph_name=? AND metric=?",
        (VIEW_CORPUS_CITATION, PAGERANK),
    )
    con.executemany(
        "INSERT INTO node_metrics(graph_name, node_type, node_id, metric, "
        "value, computed_at) VALUES (?, 'document', ?, ?, ?, ?)",
        [
            (VIEW_CORPUS_CITATION, doc_id, PAGERANK, round(float(score), 8), now)
            for doc_id, score in scores.items()
        ],
    )
    _set_status(con, projection="metrics", scope_id=VIEW_CORPUS_CITATION, status="fresh")


def refresh_h_index(con: sqlite3.Connection) -> None:
    """h-index per author over their authored documents' citation_count."""
    refresh_citation_count(con)
    cite_by_doc = dict(con.execute(
        "SELECT node_id, value FROM node_metrics "
        "WHERE graph_name=? AND metric=?",
        (VIEW_CORPUS_CITATION, CITATION_COUNT),
    ))
    by_author: dict[str, list[float]] = {}
    for r in con.execute(
        "SELECT author_id, doc_id FROM document_authors",
    ):
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


def view_status(con: sqlite3.Connection, view: str) -> dict | None:
    row = con.execute(
        "SELECT projection, scope_id, status, updated_at FROM projection_status "
        "WHERE projection='metrics' AND scope_id=?",
        (view,),
    ).fetchone()
    return dict(row) if row else None


def is_stale(con: sqlite3.Connection, view: str) -> bool:
    """Return True when the metrics for *view* haven't been refreshed yet."""
    s = view_status(con, view)
    return not s or s["status"] != "fresh"
