"""Tests for the soft boilerplate filter on the fluent ``KnowledgeGraph`` API.

Boilerplate-flagged chunks are excluded from chunk querysets by default;
``include_boilerplate=True`` opts back in. ``kg.source(d).abstract_chunk()``
returns the canonical abstract chunk via the same data invariant.
"""

from __future__ import annotations

from wikify.corpus.graph import KnowledgeGraph
from wikify.corpus.store import Store
from wikify.corpus.store.kg import SqliteGraphBackend
from wikify.models import Chunk, Document


def _kg() -> KnowledgeGraph:
    """Build a minimal KG with one source + 4 chunks, two flagged."""
    store = Store(":memory:")
    doc = Document(
        id="d1", source_path="d1.pdf", kind="pdf",
        title="Doc 1", metadata={},
        markdown_path="m/d1.md", image_dir="i/d1/",
    )
    store.upsert_document(doc)
    chunks = [
        Chunk(id="d1#c0", doc_id="d1", ord=0, text="abstract",
              char_span=(0, 1), section_path=[],
              section_type="abstract", is_boilerplate=False),
        Chunk(id="d1#c1", doc_id="d1", ord=1, text="boiler",
              char_span=(0, 1), section_path=[],
              section_type="body", is_boilerplate=True),
        Chunk(id="d1#c2", doc_id="d1", ord=2, text="body",
              char_span=(0, 1), section_path=[],
              section_type="body", is_boilerplate=False),
        Chunk(id="d1#c3", doc_id="d1", ord=3, text="boiler 2",
              char_span=(0, 1), section_path=[],
              section_type="body", is_boilerplate=True),
    ]
    store.upsert_chunks(chunks)
    store.upsert_chunk_edges("d1")
    backend = SqliteGraphBackend(store.con)
    kg = KnowledgeGraph(backend=backend)
    kg._owned_store = store  # keep connection alive
    return kg


# --- default-filter behaviour ---------------------------------------------


def test_kg_chunks_excludes_boilerplate_by_default():
    kg = _kg()
    ids = kg.chunks().ids()
    assert "d1#c1" not in ids
    assert "d1#c3" not in ids
    assert set(ids) == {"d1#c0", "d1#c2"}


def test_kg_chunks_count_excludes_boilerplate():
    assert _kg().chunks().count() == 2


def test_source_chunks_excludes_boilerplate_by_default():
    kg = _kg()
    ids = kg.source("d1").chunks().ids()
    assert set(ids) == {"d1#c0", "d1#c2"}


def test_collect_excludes_boilerplate_by_default():
    """``.collect()`` materializes filtered results."""
    chunks = _kg().chunks().collect()
    assert all(not c.get("is_boilerplate", False) for c in chunks)
    assert len(chunks) == 2


# --- opt-in to include boilerplate ----------------------------------------


def test_include_boilerplate_via_kg_chunks_kwarg():
    ids = _kg().chunks(include_boilerplate=True).ids()
    assert set(ids) == {"d1#c0", "d1#c1", "d1#c2", "d1#c3"}


def test_include_boilerplate_via_source_chunks_kwarg():
    ids = _kg().source("d1").chunks(include_boilerplate=True).ids()
    assert set(ids) == {"d1#c0", "d1#c1", "d1#c2", "d1#c3"}


def test_with_boilerplate_method_opts_in():
    """Existing queryset can be re-scoped to include boilerplate."""
    kg = _kg()
    qb = kg.chunks()
    assert qb.count() == 2
    assert qb.with_boilerplate().count() == 4


# --- abstract_chunk() fluent accessor -------------------------------------


def test_abstract_chunk_returns_the_canonical_abstract():
    chunk = _kg().source("d1").abstract_chunk()
    assert chunk is not None
    assert chunk["id"] == "d1#c0"
    assert chunk["section_type"] == "abstract"


def test_abstract_chunk_returns_none_when_no_abstract_tagged():
    """A source with no body-bearing chunks yields None."""
    store = Store(":memory:")
    doc = Document(
        id="d2", source_path="d2.pdf", kind="pdf",
        title="Empty doc", metadata={},
        markdown_path="m/d2.md", image_dir="i/d2/",
    )
    store.upsert_document(doc)
    backend = SqliteGraphBackend(store.con)
    kg = KnowledgeGraph(backend=backend)
    kg._owned_store = store
    assert kg.source("d2").abstract_chunk() is None


# --- non-chunk querysets are unaffected -----------------------------------


def test_filter_does_not_apply_to_source_querysets():
    """The filter is gated on node_type=CHUNK; sources/sections etc.
    pass through untouched."""
    kg = _kg()
    sources = kg.sources().ids()
    assert sources == ["d1"]
