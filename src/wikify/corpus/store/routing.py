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
_DEFAULT = _SQLITE


def query_backend() -> str:
    """Return the active backend.

    Default is ``sqlite`` (Phase 7 cutover). Set
    ``WIKIFY_QUERY_BACKEND=legacy`` to force the NetworkX/NPZ path.
    """
    raw = os.environ.get(_BACKEND_ENV)
    val = (raw or _DEFAULT).strip().lower()
    if val not in {_LEGACY, _SQLITE}:
        raise ValueError(
            f"unknown {_BACKEND_ENV}={val!r}; expected 'legacy' or 'sqlite'",
        )
    return val


def is_sqlite_explicit() -> bool:
    """True iff the user has explicitly set the env to sqlite."""
    raw = os.environ.get(_BACKEND_ENV)
    return raw is not None and raw.strip().lower() == _SQLITE


def is_sqlite() -> bool:
    return query_backend() == _SQLITE


def sqlite_available(corpus_root: Path) -> bool:
    return (corpus_root / "wikify.db").exists()


def use_sqlite(corpus_root: Path) -> bool:
    """Phase-7 default: use sqlite when the env permits AND wikify.db exists.

    - explicit ``sqlite``: always sqlite (caller fails if db missing)
    - explicit ``legacy``: always legacy
    - unset (default): sqlite when available, else legacy
    """
    if not is_sqlite():
        return False
    if is_sqlite_explicit():
        return True
    return sqlite_available(corpus_root)


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
