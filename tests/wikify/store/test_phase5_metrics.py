"""Phase 5 acceptance: cheap incremental metrics."""

from __future__ import annotations

from wikify.corpus.store import Edge, Store
from wikify.corpus.store.metrics import (
    CITATION_COUNT,
    COAUTHOR_COUNT,
    IN_DEGREE,
    OUT_DEGREE,
    VIEW_AUTHOR_COAUTHOR,
    VIEW_CORPUS_CITATION,
    get_node_metric,
    list_node_metric,
    refresh_cheap_metrics,
)
from wikify.models import Document


def _doc(doc_id: str, **meta) -> Document:
    return Document(
        id=doc_id, source_path=f"/{doc_id}.pdf", kind="pdf",
        title=f"Title {doc_id}", metadata=dict(authors=[], **meta),
        markdown_path=f"m/{doc_id}.md", image_dir=f"i/{doc_id}/",
    )


def test_citation_count_per_doc():
    s = Store(":memory:")
    s.upsert_document(_doc("a"))
    s.upsert_document(_doc("b"))
    s.upsert_document(_doc("c"))
    s.graph.upsert_edges([
        Edge("document", "a", "references", "document", "c"),
        Edge("document", "b", "references", "document", "c"),
        Edge("document", "a", "references", "document", "b"),
    ])
    refresh_cheap_metrics(s.con)
    assert get_node_metric(
        s.con, graph_name=VIEW_CORPUS_CITATION,
        node_type="document", node_id="c", metric=CITATION_COUNT,
    ) == 2.0
    assert get_node_metric(
        s.con, graph_name=VIEW_CORPUS_CITATION,
        node_type="document", node_id="a", metric=CITATION_COUNT,
    ) == 0.0
    top = list_node_metric(
        s.con, graph_name=VIEW_CORPUS_CITATION,
        node_type="document", metric=CITATION_COUNT, top_k=3,
    )
    assert top[0] == ("c", 2.0)


def test_coauthor_count():
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document_authors("d1", ["Jane Doe", "Alex Roe", "Sam Foo"])
    refresh_cheap_metrics(s.con)
    # Each pair contributes once; each author has degree (n_authors - 1)
    # because all three coauthor each other.
    assert get_node_metric(
        s.con, graph_name=VIEW_AUTHOR_COAUTHOR,
        node_type="author", node_id="jane doe", metric=COAUTHOR_COUNT,
    ) == 2.0


def test_degree_metrics_sum_correctly():
    s = Store(":memory:")
    s.graph.upsert_edges([
        Edge("document", "a", "references", "document", "b"),
        Edge("document", "a", "references", "document", "c"),
        Edge("document", "b", "references", "document", "c"),
    ])
    refresh_cheap_metrics(s.con)
    assert get_node_metric(
        s.con, graph_name="all_edges",
        node_type="document", node_id="a", metric=OUT_DEGREE,
    ) == 2.0
    assert get_node_metric(
        s.con, graph_name="all_edges",
        node_type="document", node_id="c", metric=IN_DEGREE,
    ) == 2.0


def test_metrics_repopulate_idempotently():
    s = Store(":memory:")
    s.upsert_document(_doc("a"))
    s.upsert_document(_doc("b"))
    s.graph.upsert_edges([Edge("document", "a", "references", "document", "b")])
    refresh_cheap_metrics(s.con)
    refresh_cheap_metrics(s.con)
    n_rows = s.con.execute(
        "SELECT COUNT(*) FROM node_metrics WHERE metric='citation_count'",
    ).fetchone()[0]
    assert n_rows == 2  # one row per existing document, no duplicates.
