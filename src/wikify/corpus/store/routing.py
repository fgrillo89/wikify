"""Query backend routing.

`WIKIFY_QUERY_BACKEND=sqlite` flips chunk search from the legacy NPZ +
NetworkX path to the SQLite query store. `legacy` (the default during
Phase 3) keeps the original behavior. Anything else raises so typos are
visible immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import Store
from .sync import space_id_for

_BACKEND_ENV = "WIKIFY_QUERY_BACKEND"
_LEGACY = "legacy"
_SQLITE = "sqlite"


def query_backend() -> str:
    """Return the active backend; defaults to `legacy` until Phase 7."""
    val = (os.environ.get(_BACKEND_ENV) or _LEGACY).strip().lower()
    if val not in {_LEGACY, _SQLITE}:
        raise ValueError(
            f"unknown {_BACKEND_ENV}={val!r}; expected 'legacy' or 'sqlite'",
        )
    return val


def is_sqlite() -> bool:
    return query_backend() == _SQLITE


def open_store(corpus_root: Path) -> Store:
    """Open a Store at <corpus_root>/wikify.db.

    Caller owns close(). Raises FileNotFoundError if wikify.db is absent —
    skill paths must fall back to legacy when this happens.
    """
    db = corpus_root / "wikify.db"
    if not db.exists():
        raise FileNotFoundError(f"wikify.db not found at {db}; rebuild the corpus")
    return Store(db)


def active_space_id(store: Store) -> str | None:
    """Pick the most-recent embedding space, or None if there are none."""
    rows = store.con.execute(
        "SELECT space_id FROM embedding_spaces ORDER BY created_at DESC LIMIT 1",
    ).fetchone()
    return rows[0] if rows else None


__all__ = ["active_space_id", "is_sqlite", "open_store", "query_backend", "space_id_for"]
