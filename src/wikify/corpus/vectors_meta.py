"""Embedder fingerprint loaded from the corpus SQLite store.

Records which embedder backend produced the active matrix so that
downstream tools (eval, query) can construct the *exact* matching
embedder. The fingerprint is read directly from the
``embedding_spaces`` table; legacy ``"sentence_transformers"`` entries
are aliased to ``"fastembed"`` by ``embedder_for`` (same model, same
dimension).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VectorsMeta:
    backend: str  # "hash" | "fastembed"
    dim: int
    model: str | None = None


def read_meta(sqlite_path: Path) -> VectorsMeta | None:
    """Return the active embedding-space fingerprint, or ``None``.

    ``None`` covers two cases: the corpus has no ``wikify.db`` yet, or
    the DB exists but no embedding space row has been written.
    """
    if not sqlite_path.exists():
        return None
    try:
        con = sqlite3.connect(sqlite_path)
        try:
            row = con.execute(
                "SELECT backend, model, dim FROM embedding_spaces "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return VectorsMeta(
        backend=str(row[0]),
        model=row[1],
        dim=int(row[2] or 0),
    )
