"""SQLite query store for the corpus.

`wikify.db` lives at `<corpus_root>/wikify.db`. One file holds canonical
entity rows (documents/chunks/authors/bib_entries/assets), FTS5 indexes,
embeddings, the graph_edges table, and the metric projections. See
``schema.py`` for the DDL and ``connection.py`` for the per-connection
PRAGMA contract.
"""

from .connection import connect, transaction
from .schema import SCHEMA_VERSION, apply_schema

__all__ = ["SCHEMA_VERSION", "apply_schema", "connect", "transaction"]
