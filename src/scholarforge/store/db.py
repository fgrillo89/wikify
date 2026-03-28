"""SQLite database setup and session management."""

from sqlmodel import Session, SQLModel, create_engine

from scholarforge.config import settings
from scholarforge.store.models import Citation, FigureRef  # noqa: F401 — ensure tables created

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        _engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)
        SQLModel.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    return Session(get_engine())
