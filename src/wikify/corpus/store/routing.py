"""SQLite query store helpers."""

from __future__ import annotations

from pathlib import Path

from . import Store
from .sync import space_id_for


def open_store(corpus_root: Path) -> Store:
    """Open the corpus Store at ``<corpus_root>/wikify.db``.

    Raises FileNotFoundError if the database is missing. Callers that
    need a graceful "no DB yet" path should check
    :func:`sqlite_available` first.
    """
    db = corpus_root / "wikify.db"
    if not db.exists():
        raise FileNotFoundError(f"wikify.db not found at {db}; rebuild the corpus")
    return Store(db)


def sqlite_available(corpus_root: Path) -> bool:
    return (corpus_root / "wikify.db").exists()


def active_space_id(store: Store) -> str | None:
    """Pick the most-recent embedding space, or None if there are none."""
    rows = store.con.execute(
        "SELECT space_id FROM embedding_spaces ORDER BY created_at DESC LIMIT 1",
    ).fetchone()
    return rows[0] if rows else None


__all__ = ["active_space_id", "open_store", "space_id_for", "sqlite_available"]
