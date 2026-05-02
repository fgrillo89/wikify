"""SQLite connection helpers.

Every connection runs the locked PRAGMA block before the caller sees it.
Pragmas are per-connection in SQLite, so concentrating them here is the
only way to make them stick.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 268435456",
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection and apply the locked PRAGMA block.

    `:memory:` is accepted; WAL is silently ignored on memory DBs by SQLite.
    """
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    for stmt in PRAGMAS:
        con.execute(stmt)
    return con


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a unit of work in BEGIN/COMMIT with rollback on exception.

    Used everywhere upserts touch more than one row (e.g. document ->
    chunks + edges + fts + embeddings).
    """
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
