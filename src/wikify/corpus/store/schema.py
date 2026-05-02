"""DDL for the corpus query store.

`apply_schema(con)` is idempotent: it runs against an empty file, against
a partially-populated dev DB, and against a fully-populated production DB
without errors. SQLite's `IF NOT EXISTS` makes that safe.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1


CORE_DDL = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  source_path TEXT,
  source_kind TEXT,
  doc_type TEXT,
  title TEXT,
  abstract TEXT,
  tldr TEXT,
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,
  publisher TEXT,
  doi TEXT,
  url TEXT,
  n_chunks INTEGER,
  n_tokens INTEGER,
  metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS documents_doi ON documents(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS documents_year ON documents(year);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  section_path_json TEXT,
  section_type TEXT,
  char_start INTEGER,
  char_end INTEGER,
  token_count INTEGER,
  is_boilerplate INTEGER DEFAULT 0,
  equation_ids_json TEXT,
  metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS chunks_doc_ord ON chunks(doc_id, ord);
CREATE INDEX IF NOT EXISTS chunks_section_type ON chunks(section_type);

CREATE TABLE IF NOT EXISTS authors (
  author_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS document_authors (
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  position INTEGER,
  role TEXT,
  PRIMARY KEY (doc_id, author_id, position)
);
CREATE INDEX IF NOT EXISTS document_authors_author ON document_authors(author_id, doc_id);

CREATE TABLE IF NOT EXISTS bib_entries (
  bib_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ord INTEGER,
  local_key TEXT,
  raw_text TEXT,
  title TEXT,
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,
  publisher TEXT,
  doi TEXT,
  url TEXT,
  target_doc_id TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,
  confidence REAL,
  resolution TEXT,
  bib_json TEXT
);
CREATE INDEX IF NOT EXISTS bib_entries_doc_ord ON bib_entries(doc_id, ord);
CREATE INDEX IF NOT EXISTS bib_entries_doi ON bib_entries(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS bib_entries_target ON bib_entries(target_doc_id)
  WHERE target_doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bib_entries_unresolved ON bib_entries(target_doc_id, doi)
  WHERE target_doc_id IS NULL;

CREATE TABLE IF NOT EXISTS chunk_citations (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  bib_id TEXT NOT NULL REFERENCES bib_entries(bib_id) ON DELETE CASCADE,
  marker_text TEXT,
  char_start INTEGER,
  char_end INTEGER,
  context TEXT,
  PRIMARY KEY (chunk_id, bib_id, marker_text, char_start)
);
CREATE INDEX IF NOT EXISTS chunk_citations_bib ON chunk_citations(bib_id, chunk_id);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  asset_type TEXT,
  ord INTEGER,
  page INTEGER,
  path TEXT,
  caption TEXT,
  content TEXT,
  metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS assets_doc ON assets(doc_id, asset_type, ord);

CREATE TABLE IF NOT EXISTS chunk_assets (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
  relation TEXT,
  confidence REAL,
  PRIMARY KEY (chunk_id, asset_id, relation)
);

CREATE TABLE IF NOT EXISTS embedding_spaces (
  space_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  model TEXT,
  dim INTEGER NOT NULL,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
  space_id TEXT NOT NULL REFERENCES embedding_spaces(space_id) ON DELETE CASCADE,
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (space_id, node_type, node_id)
);
CREATE INDEX IF NOT EXISTS embeddings_space_type ON embeddings(space_id, node_type);

CREATE TABLE IF NOT EXISTS graph_edges (
  src_type TEXT NOT NULL,
  src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  weight REAL DEFAULT 1.0,
  ord INTEGER,
  meta_json TEXT,
  PRIMARY KEY (src_type, src_id, kind, dst_type, dst_id)
);
CREATE INDEX IF NOT EXISTS graph_out  ON graph_edges(src_type, src_id, kind);
CREATE INDEX IF NOT EXISTS graph_in   ON graph_edges(dst_type, dst_id, kind);
CREATE INDEX IF NOT EXISTS graph_kind ON graph_edges(kind);

CREATE TABLE IF NOT EXISTS graph_views (
  graph_name TEXT PRIMARY KEY,
  description TEXT,
  node_types_json TEXT,
  edge_kinds_json TEXT,
  directed INTEGER,
  weighted INTEGER,
  params_json TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS node_metrics (
  graph_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, node_type, node_id, metric)
);

CREATE TABLE IF NOT EXISTS edge_metrics (
  graph_name TEXT NOT NULL,
  src_type TEXT NOT NULL, src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, src_type, src_id, kind, dst_type, dst_id, metric)
);

CREATE TABLE IF NOT EXISTS projection_status (
  projection TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT,
  error_json TEXT,
  PRIMARY KEY (projection, scope_type, scope_id)
);

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title,
  abstract,
  content='documents',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);
"""


def apply_schema(con: sqlite3.Connection) -> None:
    """Create every table/index/virtual-table the store relies on.

    Idempotent. Safe to run on every `connect()` for cheap migration.
    """
    con.executescript(CORE_DDL)
    con.executescript(FTS_DDL)
    con.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
