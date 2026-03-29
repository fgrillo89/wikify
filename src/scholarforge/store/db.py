"""SQLite database setup and session management.

Uses a DatabaseManager class instead of global variables.
Callers should prefer dependency injection where practical;
the module-level ``get_session()`` is a convenience that delegates
to the default manager.
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
    where needed. A default instance is available via ``default_manager()``.
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


# ── Default instance ─────────────────────────────────────────────────────────

_default: DatabaseManager | None = None


def default_manager() -> DatabaseManager:
    """Return the default DatabaseManager (lazy-created, uses settings)."""
    global _default  # noqa: PLW0603
    if _default is None:
        _default = DatabaseManager()
    return _default


# ── Backward-compatible module-level functions ───────────────────────────────


def get_engine():
    """Return the default SQLAlchemy engine."""
    return default_manager().engine


def get_session() -> Session:
    """Return a new session from the default manager."""
    return default_manager().session()
