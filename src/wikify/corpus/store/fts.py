"""FTS5 BM25 search and reciprocal rank fusion.

External-content over `chunks` and `documents`. `bm25()` returns lower-is-
better scores; we negate when fusing.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

RRF_K_DEFAULT = 60


def fts_rebuild(con: sqlite3.Connection) -> None:
    """Force a full FTS rebuild after a bulk load."""
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    con.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")


def fts_refresh_chunk(con: sqlite3.Connection, chunk_id: str) -> None:
    """Push a single chunk's text into the FTS index after upsert."""
    con.execute(
        "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
        "SELECT 'delete', rowid, text FROM chunks WHERE chunk_id = ?",
        (chunk_id,),
    )
    con.execute(
        "INSERT INTO chunks_fts(rowid, text) "
        "SELECT rowid, text FROM chunks WHERE chunk_id = ?",
        (chunk_id,),
    )


def fts_refresh_document(con: sqlite3.Connection, doc_id: str) -> None:
    con.execute(
        "INSERT INTO documents_fts(documents_fts, rowid, title, abstract) "
        "SELECT 'delete', rowid, title, abstract FROM documents WHERE doc_id = ?",
        (doc_id,),
    )
    con.execute(
        "INSERT INTO documents_fts(rowid, title, abstract) "
        "SELECT rowid, title, abstract FROM documents WHERE doc_id = ?",
        (doc_id,),
    )


def search_chunks_bm25(
    con: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    *,
    doc_id: str | None = None,
) -> list[tuple[str, float]]:
    """BM25 search against chunks_fts. Returns (chunk_id, score).

    When *doc_id* is set, the result is scoped to chunks of that one
    document via a cheap `chunks.doc_id = ?` predicate on the join.
    """
    if not query.strip():
        return []
    if doc_id is None:
        sql = (
            "SELECT chunks.chunk_id, bm25(chunks_fts) AS s "
            "FROM chunks_fts JOIN chunks ON chunks.rowid = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? "
            "ORDER BY s LIMIT ?"
        )
        params: tuple = (query, top_k)
    else:
        sql = (
            "SELECT chunks.chunk_id, bm25(chunks_fts) AS s "
            "FROM chunks_fts JOIN chunks ON chunks.rowid = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? AND chunks.doc_id = ? "
            "ORDER BY s LIMIT ?"
        )
        params = (query, doc_id, top_k)
    try:
        return [(r[0], float(r[1])) for r in con.execute(sql, params)]
    except sqlite3.OperationalError:
        # FTS5 query parse error (`-` as NOT, unbalanced quote, etc.).
        # Surface as "no hits" so callers can keep going.
        return []


def search_documents_bm25(
    con: sqlite3.Connection,
    query: str,
    top_k: int = 10,
    *,
    title_weight: float = 4.0,
    abstract_weight: float = 1.0,
) -> list[tuple[str, float]]:
    if not query.strip():
        return []
    sql = (
        "SELECT documents.doc_id, bm25(documents_fts, ?, ?) AS s "
        "FROM documents_fts JOIN documents ON documents.rowid = documents_fts.rowid "
        "WHERE documents_fts MATCH ? "
        "ORDER BY s LIMIT ?"
    )
    return [
        (r[0], float(r[1])) for r in con.execute(
            sql, (title_weight, abstract_weight, query, top_k),
        )
    ]


def rrf_fuse(
    rankings: list[list[tuple[str, float]]],
    *,
    k: int = RRF_K_DEFAULT,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.

    Each ranking is a list of (id, _score). RRF score for an id is
    sum over rankings of 1 / (k + rank_within_ranking). Stable across
    runs given identical inputs.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, (item, _) in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[:top_k]


def hybrid_search_chunks(
    con: sqlite3.Connection,
    query: str,
    query_vec: np.ndarray | None,
    *,
    vector_index: Any | None = None,
    top_k: int = 10,
    pool: int = 200,
    k_rrf: int = RRF_K_DEFAULT,
) -> list[tuple[str, float]]:
    """RRF over chunk-level BM25 + vector + document-level BM25 (rolled up).

    Document-level BM25 hits are expanded to their constituent chunks
    (in chunk-ord order) before fusion, with the document's BM25 rank
    propagated to every emitted chunk.
    """
    bm_chunks = search_chunks_bm25(con, query, top_k=pool)
    bm_docs = search_documents_bm25(con, query, top_k=pool)
    rolled: list[tuple[str, float]] = []
    for doc_id, score in bm_docs:
        for r in con.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,),
        ):
            rolled.append((r[0], score))
    rankings: list[list[tuple[str, float]]] = [bm_chunks, rolled]
    if query_vec is not None and vector_index is not None:
        rankings.append(vector_index.search(query_vec, top_k=pool))
    return rrf_fuse(rankings, k=k_rrf, top_k=top_k)
