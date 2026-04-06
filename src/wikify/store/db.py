"""SQLite database setup and session management.

Uses a DatabaseManager class for the engine lifecycle.
Module-level ``get_engine()`` and ``get_session()`` delegate to a
single module-level instance ``_db``.  Prefer dependency injection
(pass a DatabaseManager explicitly) when you need to swap it in tests.
"""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from wikify.config import settings
from wikify.store.models import (  # noqa: F401 — ensure tables created
    Campaign,
    ChunkMiningLog,
    Citation,
    ConceptEvidence,
    ConceptRecord,
    ConceptRelation,
    DomainCluster,
    DomainPersona,
    EpochLog,
    Equation,
    ExtractionGap,
    FigureRef,
    GeneratedOutput,
    JournalTemplate,
    PaperTopic,
    ParameterExtraction,
    Project,
    ProjectPaper,
    SourceCoverage,
    TopologySnapshot,
    WikiArticle,
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
            _run_migrations(self._engine)
        return self._engine

    def session(self) -> Session:
        """Create a new session bound to this manager's engine."""
        return Session(self.engine)


def _run_migrations(engine) -> None:
    """Apply lightweight schema migrations for new columns.

    SQLModel's create_all only creates missing tables, not columns.
    This adds columns introduced after the initial schema.
    """
    import sqlalchemy

    with engine.connect() as conn:
        # Migration 1: Paper.origin column (added 2026-03-30)
        try:
            conn.execute(sqlalchemy.text("SELECT origin FROM paper LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text("ALTER TABLE paper ADD COLUMN origin VARCHAR DEFAULT 'corpus'")
            )
            conn.commit()

        # Migration 2: Paper.section_summaries column (added 2026-04-02)
        try:
            conn.execute(sqlalchemy.text("SELECT section_summaries FROM paper LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text(
                    "ALTER TABLE paper ADD COLUMN section_summaries VARCHAR DEFAULT '{}'"
                )
            )
            conn.commit()

        # Migration 3: WikiArticle.domain column (added 2026-04-03)
        try:
            conn.execute(sqlalchemy.text("SELECT domain FROM wikiarticle LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text("ALTER TABLE wikiarticle ADD COLUMN domain VARCHAR DEFAULT ''")
            )
            conn.commit()

        # Migration 4: ConceptRecord.domains column (added 2026-04-03)
        try:
            conn.execute(sqlalchemy.text("SELECT domains FROM conceptrecord LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text(
                    "ALTER TABLE conceptrecord ADD COLUMN domains VARCHAR DEFAULT '[]'"
                )
            )
            conn.commit()

        # Migration 5: EpochLog.template_delta column (added 2026-04-03)
        try:
            conn.execute(sqlalchemy.text("SELECT template_delta FROM epochlog LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text(
                    "ALTER TABLE epochlog ADD COLUMN template_delta FLOAT DEFAULT 0.0"
                )
            )
            conn.commit()

        # Migration 6: Figure.llm_description column (added 2026-04-06)
        try:
            conn.execute(sqlalchemy.text("SELECT llm_description FROM figure LIMIT 1"))
        except Exception:  # noqa: BLE001
            conn.execute(
                sqlalchemy.text(
                    "ALTER TABLE figure ADD COLUMN llm_description VARCHAR DEFAULT NULL"
                )
            )
            conn.commit()


# ── Module-level instance ─────────────────────────────────────────────────────

_db = DatabaseManager()


# ── Module-level convenience functions ───────────────────────────────────────


def get_engine():
    """Return the SQLAlchemy engine from the module-level DatabaseManager."""
    return _db.engine


def get_session() -> Session:
    """Return a new session from the module-level DatabaseManager."""
    return _db.session()
