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

from ...corpus.store.connection import connect

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
    return con


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
    now = datetime.now(UTC).isoformat()
    existing = con.execute(
        "SELECT rowid, created_at, title, body FROM wiki_pages WHERE page_id = ?",
        (page_id,),
    ).fetchone()
    fm_json = json.dumps(frontmatter or {})
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
