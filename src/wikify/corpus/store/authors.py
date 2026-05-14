"""Authors and document_authors CRUD.

Author rows are derived from `documents.metadata['authors']`. The
canonical id is the normalized author key from ``author_key`` defined
in this module — it's the single source of truth for the projection,
and ``corpus.graph_build``, ``corpus.store.kg``, and
``bundle.draft.author_context`` all import from here. Display name
keeps the original casing so renderers don't have to re-case.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def author_key(name: str) -> str:
    """Stable id for an author display name; matches graph_build._author_key.

    Collapses hyphens to nothing so romanized Chinese / Korean names
    that publishers print either as ``Tianyu Wang`` or ``Tian-Yu Wang``
    (and similar pairs) get the same key. Truly distinct Western
    hyphenated surnames (``Garcia-Lopez``) collide with the merged form
    (``Garcialopez``), but the same merge would happen on any sane
    casefold of those tokens, and the Chinese-romanization case
    dominates by an order of magnitude in scientific bylines.
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = n.replace("-", "").replace("‐", "")  # ASCII + non-breaking hyphen
    n = re.sub(r"\s+", " ", n).strip().rstrip(",.; ")
    n = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", n)
    key = _NORM_RE.sub(" ", n.lower()).strip()
    return re.sub(r"\s+", " ", key)


def upsert_author(
    con: sqlite3.Connection,
    author_id: str,
    display_name: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO authors(author_id, display_name, metadata_json) VALUES (?, ?, ?)",
        (author_id, display_name, json.dumps(metadata or {})),
    )


def upsert_document_authors(
    con: sqlite3.Connection,
    doc_id: str,
    authors: list[str],
) -> list[str]:
    """Replace a doc's author rows. Returns the assigned author_ids in order."""
    con.execute("DELETE FROM document_authors WHERE doc_id = ?", (doc_id,))
    seen: set[tuple[str, int]] = set()
    out: list[str] = []
    for pos, raw in enumerate(authors or []):
        if not raw:
            continue
        aid = author_key(str(raw))
        if not aid:
            continue
        upsert_author(con, aid, str(raw))
        key = (aid, pos)
        if key in seen:
            continue
        seen.add(key)
        con.execute(
            "INSERT OR REPLACE INTO document_authors(doc_id, author_id, position, role) "
            "VALUES (?, ?, ?, ?)",
            (doc_id, aid, pos, "author"),
        )
        out.append(aid)
    return out


def get_authors_for_document(con: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    sql = (
        "SELECT a.author_id, a.display_name, da.position, da.role "
        "FROM document_authors da JOIN authors a USING (author_id) "
        "WHERE da.doc_id = ? ORDER BY da.position"
    )
    return [dict(r) for r in con.execute(sql, (doc_id,))]


def get_documents_for_author(con: sqlite3.Connection, author_id: str) -> list[str]:
    return [
        r[0] for r in con.execute(
            "SELECT doc_id FROM document_authors WHERE author_id = ? ORDER BY doc_id",
            (author_id,),
        )
    ]


def upsert_coauthor_edges(con: sqlite3.Connection, doc_id: str) -> None:
    """Idempotent (re)insert of coauthor edges among the authors of one doc.

    Convention: `coauthor` is undirected; stored once with `src_id < dst_id`.
    """
    rows = list(con.execute(
        "SELECT author_id FROM document_authors WHERE doc_id = ? ORDER BY position",
        (doc_id,),
    ))
    ids = sorted({r[0] for r in rows})
    edges = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            edges.append((ids[i], ids[j]))
    if not edges:
        return
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('author', ?, 'coauthor', 'author', ?)",
        edges,
    )
