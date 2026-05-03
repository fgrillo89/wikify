"""Document and chunk CRUD against the SQLite query store.

Source-of-truth fields: ``src/wikify/models.py`` (Document, Chunk).
Query-driving fields are promoted to columns; everything else lives in
`metadata_json` / `equation_ids_json` so the row stays cheap to read.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...models import Chunk, Document

_DOC_PROMOTED = {
    "source_path", "source_kind", "doc_type", "title", "abstract", "tldr",
    "authors_json", "year", "container_title", "publisher", "doi", "url",
    "n_chunks", "n_tokens",
}


def _norm_doi(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v or None


def upsert_document(con: sqlite3.Connection, doc: Document) -> None:
    """Insert/replace a `documents` row from a Document dataclass."""
    meta = dict(doc.metadata or {})
    authors_raw = meta.pop("authors", None)
    year = meta.pop("year", None)
    venue = meta.pop("venue", None)
    publisher = meta.pop("publisher", None)
    doi = _norm_doi(meta.pop("doi", None))
    url = meta.pop("url", None)
    doc_type = meta.pop("doc_type", "unknown")
    container = meta.pop("container_title", venue)
    row = {
        "doc_id": doc.id,
        "source_path": doc.source_path,
        "source_kind": doc.kind,
        "doc_type": doc_type,
        "title": doc.title,
        "abstract": doc.abstract or None,
        "tldr": doc.tldr or None,
        "authors_json": json.dumps(authors_raw or []),
        "year": int(year) if year is not None else None,
        "container_title": container,
        "publisher": publisher,
        "doi": doi,
        "url": url,
        "n_chunks": doc.n_chunks or 0,
        "n_tokens": doc.n_tokens or 0,
        "metadata_json": json.dumps(meta, default=str),
    }
    cols = ",".join(row.keys())
    placeholders = ",".join(":" + k for k in row.keys())
    con.execute(f"INSERT OR REPLACE INTO documents({cols}) VALUES ({placeholders})", row)


def get_document(con: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_documents(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("SELECT * FROM documents ORDER BY doc_id")]


def delete_document(con: sqlite3.Connection, doc_id: str) -> None:
    """Delete a document; FK cascade clears chunks/bib_entries/assets/etc."""
    con.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    con.execute(
        "DELETE FROM graph_edges WHERE (src_type='document' AND src_id=?) "
        "OR (dst_type='document' AND dst_id=?)",
        (doc_id, doc_id),
    )


def upsert_chunks(con: sqlite3.Connection, chunks: list[Chunk]) -> None:
    """Insert/replace chunks as a single statement-batched transaction."""
    rows = []
    for ck in chunks:
        rows.append({
            "chunk_id": ck.id,
            "doc_id": ck.doc_id,
            "ord": ck.ord,
            "text": ck.text,
            "section_path_json": json.dumps(list(ck.section_path or [])),
            "section_type": ck.section_type or "body",
            "char_start": int(ck.char_span[0]) if ck.char_span else None,
            "char_end": int(ck.char_span[1]) if ck.char_span else None,
            "token_count": None,
            "is_boilerplate": int(bool(ck.is_boilerplate)),
            "equation_ids_json": json.dumps(list(ck.equation_ids or [])),
            "metadata_json": None,
        })
    if not rows:
        return
    cols = ",".join(rows[0].keys())
    placeholders = ",".join(":" + k for k in rows[0].keys())
    con.executemany(
        f"INSERT OR REPLACE INTO chunks({cols}) VALUES ({placeholders})", rows,
    )


def get_chunks(con: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    return [
        dict(r) for r in con.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,),
        )
    ]


def get_chunk(con: sqlite3.Connection, chunk_id: str) -> dict[str, Any] | None:
    row = con.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
    return dict(row) if row else None


def all_chunks(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute("SELECT * FROM chunks ORDER BY doc_id, ord")]
