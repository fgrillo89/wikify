"""Embedding-space rows + in-RAM (n, d) numpy matrix cache for cosine search.

Vectors are stored as float32 little-endian BLOBs in `embeddings` and
unit-normalized at write time. On the first query in a process we decode
all rows for the active space into one contiguous matrix; subsequent
queries are matrix @ query_vec. Behind ``Store.vector_index()`` so an
hnswlib sidecar is a one-file change later.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import numpy as np

FLOAT32 = np.dtype("<f4")


def upsert_embedding_space(
    con: sqlite3.Connection,
    space_id: str,
    backend: str,
    model: str | None,
    dim: int,
) -> None:
    con.execute(
        "INSERT OR REPLACE INTO embedding_spaces(space_id, backend, model, dim, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (space_id, backend, model, dim, datetime.now(UTC).isoformat()),
    )


def get_embedding_space(con: sqlite3.Connection, space_id: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM embedding_spaces WHERE space_id = ?", (space_id,),
    ).fetchone()
    return dict(row) if row else None


def list_embedding_spaces(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM embedding_spaces ORDER BY created_at")]


def encode_vector(vec: np.ndarray) -> bytes:
    """Float32 LE, unit-normalized, contiguous bytes."""
    arr = np.ascontiguousarray(vec, dtype=FLOAT32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = (arr / n).astype(FLOAT32, copy=False)
    return arr.tobytes(order="C")


def decode_vector(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=FLOAT32).reshape(dim)


def upsert_embeddings(
    con: sqlite3.Connection,
    space_id: str,
    items: list[tuple[str, str, np.ndarray]],
) -> None:
    """Items are (node_type, node_id, vector). Vectors are unit-normalized at write."""
    rows = [(space_id, nt, nid, encode_vector(v)) for (nt, nid, v) in items]
    con.executemany(
        "INSERT OR REPLACE INTO embeddings(space_id, node_type, node_id, vector) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def delete_embeddings_for_node(
    con: sqlite3.Connection, space_id: str, node_type: str, node_id: str,
) -> None:
    con.execute(
        "DELETE FROM embeddings WHERE space_id=? AND node_type=? AND node_id=?",
        (space_id, node_type, node_id),
    )


class VectorIndex:
    """In-memory matrix cache for one (space_id, node_type) slice.

    Brute-force cosine: load once, matrix-multiply per query. Trades
    startup latency for per-query simplicity; the design swap-in for
    hnswlib stays a single-file change because callers see only
    `.search(query_vec, top_k)`.
    """

    def __init__(self, con: sqlite3.Connection, space_id: str, node_type: str = "chunk"):
        self.con = con
        self.space_id = space_id
        self.node_type = node_type
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None
        self._dim: int = 0

    def _load(self) -> None:
        space = get_embedding_space(self.con, self.space_id)
        if not space:
            self._ids = []
            self._matrix = np.zeros((0, 0), dtype=FLOAT32)
            self._dim = 0
            return
        self._dim = int(space["dim"])
        rows = self.con.execute(
            "SELECT node_id, vector FROM embeddings "
            "WHERE space_id = ? AND node_type = ? ORDER BY node_id",
            (self.space_id, self.node_type),
        ).fetchall()
        self._ids = [r[0] for r in rows]
        if not rows:
            self._matrix = np.zeros((0, self._dim), dtype=FLOAT32)
            return
        self._matrix = np.frombuffer(
            b"".join(r[1] for r in rows), dtype=FLOAT32,
        ).reshape(len(rows), self._dim)

    @property
    def matrix(self) -> np.ndarray:
        if self._matrix is None:
            self._load()
        assert self._matrix is not None
        return self._matrix

    @property
    def ids(self) -> list[str]:
        if self._matrix is None:
            self._load()
        return self._ids

    @property
    def dim(self) -> int:
        if self._matrix is None:
            self._load()
        return self._dim

    def invalidate(self) -> None:
        self._matrix = None
        self._ids = []

    def search(self, query_vec: np.ndarray, top_k: int = 10) -> list[tuple[str, float]]:
        """Cosine search assuming both query and stored vectors are unit-normalized."""
        if self.matrix.shape[0] == 0:
            return []
        q = np.ascontiguousarray(query_vec, dtype=FLOAT32)
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        scores = self.matrix @ q
        if top_k >= scores.shape[0]:
            order = np.argsort(-scores)
        else:
            cut = np.argpartition(-scores, top_k)[:top_k]
            order = cut[np.argsort(-scores[cut])]
        return [(self._ids[int(i)], float(scores[int(i)])) for i in order]

    def vector(self, node_id: str) -> np.ndarray | None:
        row = self.con.execute(
            "SELECT vector FROM embeddings "
            "WHERE space_id = ? AND node_type = ? AND node_id = ?",
            (self.space_id, self.node_type, node_id),
        ).fetchone()
        if not row:
            return None
        return decode_vector(row[0], self.dim)
