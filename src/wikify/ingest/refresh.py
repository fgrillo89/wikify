"""Backwards-compatibility shim -- all logic lives in pipeline.py now."""

# Re-export everything that tests and other modules import from refresh.py.
# ruff: noqa: F401
from .pipeline import (
    _dedupe_sources,
    ingest_corpus,
)
from .pipeline import (
    bind_equations_to_chunks as _bind_equations_to_chunks,
)
from .pipeline import (
    content_hash as _content_hash,
)
from .pipeline import (
    doc_id_for as _doc_id_for,
)
from .pipeline import (
    image_slug as _image_slug,
)
from .pipeline import (
    iter_sources as _iter_sources,
)
from .pipeline import (
    populate_doc_edges as _populate_doc_edges,
)
from .pipeline import (
    sections_from_chunks as _sections_from_chunks,
)
from .pipeline import (
    write_pagerank as _write_pagerank,
)

__all__ = ["ingest_corpus"]
