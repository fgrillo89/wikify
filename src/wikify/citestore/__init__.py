"""citestore -- academic citation resolution: heuristic parsing, OpenAlex, SQLite."""

from .db import DatabaseManager
from .models import ResolutionResult, Work
from .parse import parse_citation
from .resolver import AsyncResolver

__all__ = [
    "AsyncResolver",
    "DatabaseManager",
    "ResolutionResult",
    "Work",
    "parse_citation",
]
