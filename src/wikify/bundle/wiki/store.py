"""SQLite query store for wiki bundles (`<bundle_root>/wiki.db`).

Mirrors the corpus store's shape: canonical `wiki_pages` table, FTS5
external-content index, and minimal graph rows for `links_to` /
`cites_evidence` / `grounded_in`. The corpus store at
`<corpus_root>/wikify.db` can be ATTACHed for cross-DB joins.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...corpus.store.connection import connect, transaction
from .vectors import (
    WikiVectorIndex,
    active_wiki_space_id,
    list_wiki_embedding_spaces,
    upsert_wiki_embedding_space,
    upsert_wiki_embeddings,
)

__all__ = [
    "WikiVectorIndex",
    "active_wiki_space_id",
    "apply_navigation_categories",
    "export_navigation_json",
    "get_wiki_page",
    "list_wiki_categories",
    "list_wiki_category_memberships",
    "list_wiki_embedding_spaces",
    "list_wiki_pages",
    "open_wiki_store",
    "replace_wiki_category_tree",
    "search_wiki_bm25",
    "upsert_wiki_embedding_space",
    "upsert_wiki_embeddings",
    "upsert_wiki_page",
]

WIKI_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages (
  page_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  frontmatter_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS wiki_pages_kind ON wiki_pages(kind);

CREATE TABLE IF NOT EXISTS wiki_evidence (
  page_id TEXT NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
  marker TEXT NOT NULL,
  chunk_id TEXT,
  doc_id TEXT,
  quote TEXT,
  PRIMARY KEY (page_id, marker)
);
CREATE INDEX IF NOT EXISTS wiki_evidence_doc ON wiki_evidence(doc_id);
CREATE INDEX IF NOT EXISTS wiki_evidence_chunk ON wiki_evidence(chunk_id);

CREATE TABLE IF NOT EXISTS wiki_edges (
  src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  meta_json TEXT,
  PRIMARY KEY (src_id, kind, dst_type, dst_id)
);
CREATE INDEX IF NOT EXISTS wiki_edges_kind ON wiki_edges(kind);
CREATE INDEX IF NOT EXISTS wiki_edges_dst ON wiki_edges(dst_type, dst_id, kind);

CREATE TABLE IF NOT EXISTS wiki_categories (
  category_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  parent_id TEXT REFERENCES wiki_categories(category_id) ON DELETE CASCADE,
  sort_order INTEGER,
  confidence REAL,
  source TEXT,
  rationale_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS wiki_categories_parent ON wiki_categories(parent_id);

CREATE TABLE IF NOT EXISTS wiki_category_pages (
  category_id TEXT NOT NULL REFERENCES wiki_categories(category_id) ON DELETE CASCADE,
  page_id TEXT NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
  confidence REAL,
  source TEXT,
  rationale_json TEXT,
  created_at TEXT,
  updated_at TEXT,
  PRIMARY KEY (category_id, page_id)
);
CREATE INDEX IF NOT EXISTS wiki_category_pages_page ON wiki_category_pages(page_id);

CREATE TABLE IF NOT EXISTS wiki_embedding_spaces (
  space_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  model TEXT,
  dim INTEGER NOT NULL,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wiki_embeddings (
  space_id TEXT NOT NULL REFERENCES wiki_embedding_spaces(space_id) ON DELETE CASCADE,
  page_id TEXT NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
  vector BLOB NOT NULL,
  PRIMARY KEY (space_id, page_id)
);
CREATE INDEX IF NOT EXISTS wiki_embeddings_space ON wiki_embeddings(space_id);

CREATE TABLE IF NOT EXISTS wiki_graph_views (
  graph_name TEXT PRIMARY KEY,
  description TEXT,
  node_types_json TEXT,
  edge_kinds_json TEXT,
  directed INTEGER,
  weighted INTEGER,
  params_json TEXT,
  updated_at TEXT,
  confidence REAL,
  source TEXT,
  rationale_json TEXT
);

CREATE TABLE IF NOT EXISTS wiki_node_metrics (
  graph_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  confidence REAL,
  source TEXT,
  rationale_json TEXT,
  PRIMARY KEY (graph_name, node_type, node_id, metric)
);

CREATE TABLE IF NOT EXISTS wiki_edge_metrics (
  graph_name TEXT NOT NULL,
  src_type TEXT NOT NULL,
  src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  confidence REAL,
  source TEXT,
  rationale_json TEXT,
  PRIMARY KEY (graph_name, src_type, src_id, kind, dst_type, dst_id, metric)
);

CREATE TABLE IF NOT EXISTS wiki_projection_status (
  projection TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT,
  confidence REAL,
  source TEXT,
  rationale_json TEXT,
  error_json TEXT,
  PRIMARY KEY (projection, scope_type, scope_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages_fts USING fts5(
  title, body,
  content='wiki_pages',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);
"""


def open_wiki_store(path: str | Path) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(WIKI_SCHEMA)
    _ensure_wiki_category_columns(con)
    return con


def _ensure_wiki_category_columns(con: sqlite3.Connection) -> None:
    cols = {
        str(r["name"])
        for r in con.execute("PRAGMA table_info(wiki_categories)").fetchall()
    }
    if "description" not in cols:
        con.execute("ALTER TABLE wiki_categories ADD COLUMN description TEXT")
    if "sort_order" not in cols:
        con.execute("ALTER TABLE wiki_categories ADD COLUMN sort_order INTEGER")


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def upsert_wiki_page(
    con: sqlite3.Connection,
    *,
    page_id: str,
    slug: str,
    title: str,
    kind: str,
    body: str,
    frontmatter: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    links: list[str] | None = None,
) -> None:
    """Refresh page row + FTS index + evidence + outgoing edges atomically.

    All mutations land inside one BEGIN/COMMIT so readers never observe
    a half-refreshed page (e.g. new body but stale evidence) and a mid-
    upsert exception rolls everything back.
    """
    now = datetime.now(UTC).isoformat()
    fm_json = json.dumps(frontmatter or {})
    with transaction(con):
        existing = con.execute(
            "SELECT rowid, created_at, title, body FROM wiki_pages WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        if existing:
            # Update in place so rowid stays stable; tell FTS the old (title, body)
            # via 'delete' so its index forgets the previous content first.
            con.execute(
                "INSERT INTO wiki_pages_fts(wiki_pages_fts, rowid, title, body) "
                "VALUES('delete', ?, ?, ?)",
                (existing["rowid"], existing["title"], existing["body"]),
            )
            con.execute(
                "UPDATE wiki_pages SET slug=?, title=?, kind=?, body=?, "
                "frontmatter_json=?, updated_at=? WHERE page_id=?",
                (slug, title, kind, body, fm_json, now, page_id),
            )
            rowid = existing["rowid"]
        else:
            cur = con.execute(
                "INSERT INTO wiki_pages(page_id, slug, title, kind, body, "
                "frontmatter_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (page_id, slug, title, kind, body, fm_json, now, now),
            )
            rowid = cur.lastrowid
        con.execute(
            "INSERT INTO wiki_pages_fts(rowid, title, body) VALUES (?, ?, ?)",
            (rowid, title, body),
        )
        # Refresh evidence rows for this page.
        con.execute("DELETE FROM wiki_evidence WHERE page_id = ?", (page_id,))
        if evidence:
            con.executemany(
                "INSERT INTO wiki_evidence(page_id, marker, chunk_id, doc_id, quote) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        page_id,
                        ev.get("marker") or f"e{i}",
                        ev.get("chunk_id"),
                        ev.get("doc_id"),
                        (ev.get("quote") or "")[:2000],
                    )
                    for i, ev in enumerate(evidence)
                ],
            )
        # Refresh outgoing edges for this page.
        con.execute("DELETE FROM wiki_edges WHERE src_id = ?", (page_id,))
        edge_rows: list[tuple[str, str, str, str, str | None]] = []
        for ev in evidence or []:
            chunk_id = ev.get("chunk_id")
            if chunk_id:
                edge_rows.append((page_id, "cites_evidence", "chunk", chunk_id, None))
            doc_id = ev.get("doc_id")
            if doc_id:
                edge_rows.append((page_id, "grounded_in", "document", doc_id, None))
        for link in links or []:
            edge_rows.append((page_id, "links_to", "wiki_page", link, None))
        if edge_rows:
            con.executemany(
                "INSERT OR IGNORE INTO wiki_edges(src_id, kind, dst_type, dst_id, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                edge_rows,
            )


def get_wiki_page(con: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM wiki_pages WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def list_wiki_pages(
    con: sqlite3.Connection, *, kind: str | None = None,
) -> list[dict[str, Any]]:
    if kind:
        rows = con.execute(
            "SELECT * FROM wiki_pages WHERE kind = ? ORDER BY title", (kind,),
        )
    else:
        rows = con.execute("SELECT * FROM wiki_pages ORDER BY kind, title")
    return [dict(r) for r in rows]


def search_wiki_bm25(
    con: sqlite3.Connection, query: str, *, top_k: int = 10,
) -> list[tuple[str, float]]:
    if not query.strip():
        return []
    sql = (
        "SELECT wiki_pages.page_id, bm25(wiki_pages_fts, 4.0, 1.0) AS s "
        "FROM wiki_pages_fts JOIN wiki_pages ON wiki_pages.rowid = wiki_pages_fts.rowid "
        "WHERE wiki_pages_fts MATCH ? ORDER BY s LIMIT ?"
    )
    return [(r[0], float(r[1])) for r in con.execute(sql, (query, top_k))]


def replace_wiki_category_tree(
    con: sqlite3.Connection,
    categories: list[dict[str, Any]],
    memberships: list[dict[str, Any]] | None = None,
) -> None:
    """Replace persisted category hierarchy and page memberships atomically."""
    now = datetime.now(UTC).isoformat()
    category_rows = [
        (
            c["category_id"],
            c.get("name") or c["category_id"],
            c.get("description"),
            c.get("sort_order"),
            c.get("confidence"),
            c.get("source"),
            _json_text(c.get("rationale_json") or c.get("rationale")),
            now,
            now,
        )
        for c in categories
    ]
    parent_rows = [(c.get("parent_id"), c["category_id"]) for c in categories]
    membership_rows = [
        (
            m["category_id"],
            m["page_id"],
            m.get("confidence"),
            m.get("source"),
            _json_text(m.get("rationale_json") or m.get("rationale")),
            now,
            now,
        )
        for m in memberships or []
    ]
    with transaction(con):
        con.execute("DELETE FROM wiki_category_pages")
        con.execute("DELETE FROM wiki_categories")
        if category_rows:
            con.executemany(
                "INSERT INTO wiki_categories(category_id, name, description, parent_id, "
                "sort_order, confidence, source, rationale_json, created_at, updated_at) "
                "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                category_rows,
            )
            con.executemany(
                "UPDATE wiki_categories SET parent_id = ? WHERE category_id = ?",
                parent_rows,
            )
        if membership_rows:
            con.executemany(
                "INSERT INTO wiki_category_pages(category_id, page_id, confidence, source, "
                "rationale_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                membership_rows,
            )


def list_wiki_categories(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM wiki_categories ORDER BY COALESCE(parent_id, ''), name, category_id",
    ).fetchall()
    return [dict(r) for r in rows]


def list_wiki_category_memberships(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM wiki_category_pages ORDER BY category_id, page_id",
    ).fetchall()
    return [dict(r) for r in rows]


def apply_navigation_categories(con: sqlite3.Connection, navigation: dict[str, Any]) -> None:
    """Persist a validated navigation payload as queryable wiki categories."""
    categories: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []

    def visit(groups: list[dict[str, Any]], parent_id: str | None = None) -> None:
        for idx, group in enumerate(groups):
            category_id = str(group["id"])
            categories.append(
                {
                    "category_id": category_id,
                    "name": str(group.get("title") or category_id),
                    "description": str(group.get("description") or ""),
                    "parent_id": parent_id,
                    "sort_order": idx,
                    "source": "navigation",
                }
            )
            for page_id in group.get("page_ids") or []:
                memberships.append(
                    {
                        "category_id": category_id,
                        "page_id": str(page_id),
                        "source": "navigation",
                    }
                )
            visit(group.get("children") or [], category_id)

    visit(navigation.get("groups") or [])
    replace_wiki_category_tree(con, categories, memberships)


def export_navigation_json(con: sqlite3.Connection) -> dict[str, Any]:
    """Export persisted categories in render-compatible navigation shape."""
    categories = list_wiki_categories(con)
    memberships = list_wiki_category_memberships(con)
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    by_category: dict[str, list[str]] = {}
    for category in categories:
        by_parent.setdefault(category.get("parent_id"), []).append(category)
    for membership in memberships:
        by_category.setdefault(str(membership["category_id"]), []).append(
            str(membership["page_id"])
        )

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        sort_order = row.get("sort_order")
        return (
            int(sort_order) if sort_order is not None else 1_000_000,
            str(row.get("name") or row.get("category_id") or "").lower(),
        )

    def build(parent_id: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in sorted(by_parent.get(parent_id, []), key=sort_key):
            category_id = str(row["category_id"])
            out.append(
                {
                    "id": category_id,
                    "title": str(row.get("name") or category_id),
                    "description": str(row.get("description") or ""),
                    "page_ids": sorted(by_category.get(category_id, [])),
                    "children": build(category_id),
                }
            )
        return out

    assigned = {page_id for ids in by_category.values() for page_id in ids}
    all_pages = {
        str(r["page_id"])
        for r in con.execute("SELECT page_id FROM wiki_pages").fetchall()
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy": "wiki-db",
        "groups": build(None),
        "ungrouped_page_ids": sorted(all_pages - assigned),
    }
