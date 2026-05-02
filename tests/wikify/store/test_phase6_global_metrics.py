"""Phase 6 acceptance: PageRank + h-index via explicit refresh."""

from __future__ import annotations

from wikify.corpus.store import Edge, Store
from wikify.corpus.store.metrics import VIEW_CORPUS_CITATION
from wikify.corpus.store.metrics_global import (
    H_INDEX,
    PAGERANK,
    is_stale,
    refresh_h_index,
    refresh_pagerank,
)
from wikify.models import Document


def _doc(doc_id: str, *, authors: list[str] | None = None) -> Document:
    return Document(
        id=doc_id, source_path=f"/{doc_id}.pdf", kind="pdf",
        title=f"Title {doc_id}",
        metadata=dict(authors=authors or []),
        markdown_path=f"m/{doc_id}.md", image_dir=f"i/{doc_id}/",
    )


def test_pagerank_assigns_higher_score_to_more_cited_doc():
    s = Store(":memory:")
    for did in ("a", "b", "c", "d"):
        s.upsert_document(_doc(did))
    # a, b, d all cite c; c cites nothing -> c gets the most rank.
    s.graph.upsert_edges([
        Edge("document", "a", "references", "document", "c"),
        Edge("document", "b", "references", "document", "c"),
        Edge("document", "d", "references", "document", "c"),
        Edge("document", "a", "references", "document", "b"),
    ])
    refresh_pagerank(s.con)
    rows = dict(s.con.execute(
        "SELECT node_id, value FROM node_metrics "
        "WHERE graph_name=? AND metric=?",
        (VIEW_CORPUS_CITATION, PAGERANK),
    ))
    assert rows["c"] > rows["a"]
    assert rows["c"] > rows["d"]


def test_h_index_per_author():
    s = Store(":memory:")
    # Author X has 3 papers cited 3, 1, 0 times -> h = 1
    # Author Y has 4 papers each cited 5 times -> h = 4
    s.upsert_document(_doc("x1", authors=["X"]))
    s.upsert_document(_doc("x2", authors=["X"]))
    s.upsert_document(_doc("x3", authors=["X"]))
    for did in ("y1", "y2", "y3", "y4"):
        s.upsert_document(_doc(did, authors=["Y"]))
    s.upsert_document_authors("x1", ["X"])
    s.upsert_document_authors("x2", ["X"])
    s.upsert_document_authors("x3", ["X"])
    for did in ("y1", "y2", "y3", "y4"):
        s.upsert_document_authors(did, ["Y"])

    edges = []
    # x1 cited 3 times, x2 cited 1, x3 cited 0
    for src in ("c1", "c2", "c3"):
        s.upsert_document(_doc(src))
        edges.append(Edge("document", src, "references", "document", "x1"))
    s.upsert_document(_doc("c4"))
    edges.append(Edge("document", "c4", "references", "document", "x2"))
    # y* each cited 5 times
    for i, did in enumerate(("y1", "y2", "y3", "y4")):
        for j in range(5):
            cid = f"yc{i}_{j}"
            s.upsert_document(_doc(cid))
            edges.append(Edge("document", cid, "references", "document", did))
    s.graph.upsert_edges(edges)
    refresh_h_index(s.con)

    h_x = s.con.execute(
        "SELECT value FROM node_metrics WHERE node_type='author' AND metric=? AND node_id='x'",
        (H_INDEX,),
    ).fetchone()
    h_y = s.con.execute(
        "SELECT value FROM node_metrics WHERE node_type='author' AND metric=? AND node_id='y'",
        (H_INDEX,),
    ).fetchone()
    assert h_x and h_y
    assert h_x[0] == 1.0
    assert h_y[0] == 4.0


def test_stale_flag_until_refresh():
    s = Store(":memory:")
    assert is_stale(s.con, VIEW_CORPUS_CITATION)
    refresh_pagerank(s.con)
    assert not is_stale(s.con, VIEW_CORPUS_CITATION)
