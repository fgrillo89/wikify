"""Wiki page embedding helpers and in-memory cosine search."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

import numpy as np

FLOAT32 = np.dtype("<f4")


def encode_vector(vec: np.ndarray) -> bytes:
    """Float32 LE, unit-normalized, contiguous bytes."""
    arr = np.ascontiguousarray(vec, dtype=FLOAT32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = (arr / n).astype(FLOAT32, copy=False)
    return arr.tobytes(order="C")


def decode_vector(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=FLOAT32).reshape(dim)


def upsert_wiki_embedding_space(
    con: sqlite3.Connection,
    space_id: str,
    backend: str,
    model: str | None,
    dim: int,
) -> None:
    con.execute(
        "INSERT INTO wiki_embedding_spaces(space_id, backend, model, dim, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(space_id) DO UPDATE SET "
        "backend=excluded.backend, model=excluded.model, dim=excluded.dim, "
        "created_at=excluded.created_at",
        (space_id, backend, model, dim, datetime.now(UTC).isoformat()),
    )


def list_wiki_embedding_spaces(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM wiki_embedding_spaces ORDER BY created_at, space_id",
    ).fetchall()
    return [dict(r) for r in rows]


def active_wiki_space_id(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT space_id FROM wiki_embedding_spaces "
        "ORDER BY created_at DESC, space_id DESC LIMIT 1",
    ).fetchone()
    return str(row[0]) if row else None


def upsert_wiki_embeddings(
    con: sqlite3.Connection,
    space_id: str,
    items: Iterable[tuple[str, np.ndarray]],
) -> None:
    """Items are (page_id, vector). Vectors are unit-normalized at write."""
    rows = [(space_id, page_id, encode_vector(vec)) for page_id, vec in items]
    if not rows:
        return
    con.executemany(
        "INSERT OR REPLACE INTO wiki_embeddings(space_id, page_id, vector) "
        "VALUES (?, ?, ?)",
        rows,
    )


class WikiVectorIndex:
    """In-memory matrix cache for one wiki embedding space."""

    def __init__(self, con: sqlite3.Connection, space_id: str):
        self.con = con
        self.space_id = space_id
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None
        self._dim = 0

    def _load(self) -> None:
        row = self.con.execute(
            "SELECT dim FROM wiki_embedding_spaces WHERE space_id = ?",
            (self.space_id,),
        ).fetchone()
        if not row:
            self._ids = []
            self._matrix = np.zeros((0, 0), dtype=FLOAT32)
            self._dim = 0
            return
        self._dim = int(row["dim"])
        rows = self.con.execute(
            "SELECT page_id, vector FROM wiki_embeddings "
            "WHERE space_id = ? ORDER BY page_id",
            (self.space_id,),
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
        if self.matrix.shape[0] == 0 or top_k <= 0:
            return []
        q = np.ascontiguousarray(query_vec, dtype=FLOAT32)
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        scores = self.matrix @ q
        if top_k >= scores.shape[0]:
            order = np.argsort(-scores)
        else:
            cut = np.argpartition(-scores, top_k - 1)[:top_k]
            order = cut[np.argsort(-scores[cut])]
        return [(self._ids[int(i)], float(scores[int(i)])) for i in order]

    def vector(self, page_id: str) -> np.ndarray | None:
        row = self.con.execute(
            "SELECT vector FROM wiki_embeddings WHERE space_id = ? AND page_id = ?",
            (self.space_id, page_id),
        ).fetchone()
        if not row:
            return None
        return decode_vector(row[0], self.dim)
