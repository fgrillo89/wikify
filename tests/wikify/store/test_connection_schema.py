"""Smoke tests for the SQLite store connection + schema bootstrap."""

from __future__ import annotations

from wikify.corpus.store import SCHEMA_VERSION, apply_schema, connect


def test_connect_applies_pragmas(tmp_path):
    con = connect(tmp_path / "wikify.db")
    pragmas = {
        "foreign_keys": 1,
        "synchronous": 1,  # NORMAL = 1
        "busy_timeout": 5000,
        "temp_store": 2,  # MEMORY
    }
    for name, expected in pragmas.items():
        assert con.execute(f"PRAGMA {name}").fetchone()[0] == expected


def test_apply_schema_idempotent(tmp_path):
    con = connect(tmp_path / "wikify.db")
    apply_schema(con)
    apply_schema(con)  # second run must not raise
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "documents", "chunks", "authors", "document_authors",
        "bib_entries", "chunk_citations",
        "assets", "chunk_assets",
        "embedding_spaces", "embeddings",
        "graph_edges",
        "graph_views", "node_metrics", "edge_metrics", "projection_status",
        "schema_meta",
    }
    assert expected.issubset(tables)
    fts_sql = "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
    fts = {r[0] for r in con.execute(fts_sql)}
    assert fts == {"chunks_fts", "documents_fts"}
    version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    assert int(version) == SCHEMA_VERSION


def test_indexes_present(tmp_path):
    con = connect(tmp_path / "wikify.db")
    apply_schema(con)
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    for required in (
        "documents_doi", "documents_year",
        "chunks_doc_ord", "chunks_section_type",
        "document_authors_author",
        "bib_entries_doc_ord", "bib_entries_doi", "bib_entries_target", "bib_entries_unresolved",
        "chunk_citations_bib",
        "assets_doc",
        "embeddings_space_type",
        "graph_out", "graph_in", "graph_kind",
    ):
        assert required in idx, f"missing index {required}"
