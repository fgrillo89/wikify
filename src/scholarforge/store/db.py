"""SQLite database setup and session management.

Uses a DatabaseManager class for the engine lifecycle.
Module-level ``get_engine()`` and ``get_session()`` delegate to a
single module-level instance ``_db``.  Prefer dependency injection
(pass a DatabaseManager explicitly) when you need to swap it in tests.
"""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from scholarforge.config import settings
from scholarforge.store.models import (  # noqa: F401 — ensure tables created
    Citation,
    FigureRef,
    JournalTemplate,
    PaperTopic,
)


class DatabaseManager:
    """Manages the SQLite engine lifecycle.

    Designed for dependency injection: create an instance and pass it
    where needed.  The module-level ``_db`` instance is used by the
    convenience functions below.
    """

    def __init__(self, db_path: str | None = None, echo: bool = False) -> None:
        self._db_path = db_path
        self._echo = echo
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            path = self._db_path or str(settings.db_path)
            settings.ensure_dirs()
            self._engine = create_engine(f"sqlite:///{path}", echo=self._echo)
            SQLModel.metadata.create_all(self._engine)
        return self._engine

    def session(self) -> Session:
        """Create a new session bound to this manager's engine."""
        return Session(self.engine)


# ── Module-level instance ─────────────────────────────────────────────────────

_db = DatabaseManager()


# ── Module-level convenience functions ───────────────────────────────────────


def get_engine():
    """Return the SQLAlchemy engine from the module-level DatabaseManager."""
    return _db.engine


def get_session() -> Session:
    """Return a new session from the module-level DatabaseManager."""
    return _db.session()
