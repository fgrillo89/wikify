"""Fluent KnowledgeGraph API tests against the SQLite-backed backend.

Replaces the older `tests/wikify/citestore/test_graph.py` and
`tests/wikify/test_graph_build_cites.py` which poked at NetworkX-shaped
internals (`g.edges()`, `g.in_edges()`, `node["pagerank"]` populated
during build). The runtime KG now lives in `wikify.db`; metrics live in
`node_metrics`. These tests exercise the user-facing fluent API only.
"""

from __future__ import annotations

from wikify.citations.models import CitationEntry
from wikify.corpus.graph import KnowledgeGraph
from wikify.corpus.graph_build import build_knowledge_graph
from wikify.corpus.store import Edge, Store
from wikify.corpus.store.kg import SqliteGraphBackend
from wikify.corpus.store.metrics import refresh_cheap_metrics
from wikify.corpus.store.metrics_global import refresh_h_index, refresh_pagerank
from wikify.models import Chunk, Document


def _doc(doc_id: str, *, title: str | None = None, **meta) -> Document:
    return Document(
        id=doc_id, source_path=f"{doc_id}.pdf", kind="pdf",
        title=title or doc_id,
        metadata=dict(authors=meta.pop("authors", []),
                      year=meta.pop("year", None), **meta),
        markdown_path=f"m/{doc_id}.md", image_dir=f"i/{doc_id}/",
    )


def _build(docs, chunks=None, citation_index=None) -> KnowledgeGraph:
    return build_knowledge_graph(
        docs, chunks or [], vectors=None, citation_index=citation_index,
    )


def test_kg_source_and_authors_via_fluent_api():
    kg = _build([
        _doc("a", authors=["Smith, J.", "Jones, K."]),
        _doc("b", authors=["Smith, J."]),
    ])
    assert kg.source("a").exists()
    authors = sorted(kg.source("a").authors().ids())
    assert "smith j" in authors and "jones k" in authors


def test_kg_chunks_of_a_source():
    chunks = [
        Chunk(id="a/c0", doc_id="a", ord=0, text="hello",
              char_span=(0, 1), section_path=[]),
        Chunk(id="a/c1", doc_id="a", ord=1, text="world",
              char_span=(0, 1), section_path=[]),
    ]
    kg = _build([_doc("a")], chunks=chunks)
    assert sorted(kg.source("a").chunks().ids()) == ["a/c0", "a/c1"]


def test_kg_citations_create_references_edges():
    citing = _doc(
        "c", authors=["Citer, A."],
        title="Citing",
    )
    citing.citations = [
        CitationEntry(ord=0, raw_text="...", doi="10.1/a", title="A's title"),
    ]
    target = _doc("a", authors=["Smith, J."])
    target.metadata["doi"] = "10.1/a"
    kg = _build([citing, target])
    # Re-resolution by the build helper attaches the references edge once
    # both endpoints exist.
    refs = sorted(kg.source("c").references().ids())
    assert "a" in refs


def test_kg_citation_count_via_node_metrics():
    a = _doc("a")
    b = _doc("b")
    c = _doc("c")
    a.cites = ["b"]
    c.cites = ["b"]
    kg = _build([a, b, c])
    # Use the underlying store to seed `references` edges from doc.cites
    backend: SqliteGraphBackend = kg._backend  # type: ignore[assignment]
    backend.con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'references', 'document', ?)",
        [("a", "b"), ("c", "b")],
    )
    refresh_cheap_metrics(backend.con)
    refresh_pagerank(backend.con)
    refresh_h_index(backend.con)
    # Reopen so the new attrs land in node_attrs.
    fresh = SqliteGraphBackend(backend.con)
    fresh_kg = KnowledgeGraph(backend=fresh)
    cite_counts = fresh_kg.sources().citation_count()
    assert cite_counts.get("b", 0) == 2
    assert cite_counts.get("a", 0) == 0


def test_kg_pagerank_top_uses_metric():
    a, b, c = _doc("a"), _doc("b"), _doc("c")
    kg = _build([a, b, c])
    backend: SqliteGraphBackend = kg._backend  # type: ignore[assignment]
    backend.con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'references', 'document', ?)",
        [("a", "b"), ("c", "b"), ("a", "c")],
    )
    refresh_pagerank(backend.con)
    fresh = SqliteGraphBackend(backend.con)
    fresh_kg = KnowledgeGraph(backend=fresh)
    top1 = fresh_kg.sources().top(1, by="pagerank").ids()
    assert top1 == ["b"]


def test_kg_h_index_attaches_to_authors():
    a = _doc("a", authors=["X"])
    b = _doc("b", authors=["X"])
    c = _doc("c", authors=["X"])
    other = _doc("o")
    kg = _build([a, b, c, other])
    backend: SqliteGraphBackend = kg._backend  # type: ignore[assignment]
    backend.con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'references', 'document', ?)",
        [("o", "a"), ("o", "b"), ("o", "c")],
    )
    refresh_cheap_metrics(backend.con)
    refresh_h_index(backend.con)
    fresh = SqliteGraphBackend(backend.con)
    # Author X has 3 papers each cited 1 time -> h = 1.
    h_attrs = [
        fresh._node_attrs[nid].get("h_index")
        for nid in fresh.nodes_of_type("author")
    ]
    assert any(h == 1 for h in h_attrs)


def test_kg_top_n_returns_metric_order():
    docs = [_doc(f"d{i}") for i in range(5)]
    kg = _build(docs)
    backend: SqliteGraphBackend = kg._backend  # type: ignore[assignment]
    backend.con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'references', 'document', ?)",
        [
            ("d0", "d4"),
            ("d1", "d4"),
            ("d2", "d4"),
            ("d3", "d4"),
            ("d0", "d3"),
        ],
    )
    refresh_pagerank(backend.con)
    refresh_cheap_metrics(backend.con)
    fresh = SqliteGraphBackend(backend.con)
    fresh_kg = KnowledgeGraph(backend=fresh)
    top = fresh_kg.sources().top(2, by="pagerank").ids()
    assert top[0] == "d4"


def test_kg_chunks_filter_by_section_type():
    chunks = [
        Chunk(id="a/c0", doc_id="a", ord=0, text="abs", char_span=(0, 1),
              section_path=[], section_type="abstract"),
        Chunk(id="a/c1", doc_id="a", ord=1, text="body", char_span=(0, 1),
              section_path=[], section_type="body"),
    ]
    kg = _build([_doc("a")], chunks=chunks)
    abs_ids = kg.source("a").chunks().where(section_type="abstract").ids()
    assert abs_ids == ["a/c0"]


def test_kg_save_load_round_trip(tmp_path):
    from wikify.corpus.graph_build import load_knowledge_graph, save_knowledge_graph

    kg = _build([_doc("a"), _doc("b")])
    path = tmp_path / "saved.db"
    save_knowledge_graph(path, kg)
    assert path.exists()
    loaded = load_knowledge_graph(path)
    assert sorted(loaded.sources().ids()) == ["a", "b"]


def test_kg_traverse_via_graph_store_neighbors():
    """The lower-level GraphStore over the same connection still works."""
    s = Store(":memory:")
    s.graph.upsert_edges([
        Edge("document", "a", "references", "document", "b"),
    ])
    rows = s.graph.neighbors("document", "a", direction="out", kinds=["references"])
    assert any(r["dst_id"] == "b" for r in rows)
