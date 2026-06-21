"""Factual-data extraction subsystem.

Captures numeric/factual figures from a corpus into a bundle-scoped claim
store (schema-on-read), verifies each claim against its source text, and
consolidates claims into evolving "data artifact" tables that live in the
wiki and re-derive from a durable spec.

The claim store is the source of truth; a data-artifact table is a
materialized view over it (never hand-edited, always rebuildable).
"""

from .models import ArtifactSpec, DataPoint, normalize_key, parse_leading_number
from .store import DataStore

__all__ = [
    "ArtifactSpec",
    "DataPoint",
    "DataStore",
    "normalize_key",
    "parse_leading_number",
]
