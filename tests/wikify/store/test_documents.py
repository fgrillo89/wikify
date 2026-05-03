"""CRUD + cascade tests for documents/chunks/authors."""

from __future__ import annotations

from wikify.corpus.store import Store
from wikify.models import Chunk, Document


def _make_doc(doc_id: str = "d1", **meta) -> Document:
    return Document(
        id=doc_id, source_path=f"/p/{doc_id}.pdf", kind="pdf",
        title=meta.pop("title", "Sample title"),
        metadata=dict(
            authors=meta.pop("authors", ["Jane Doe", "Alex Roe"]),
            year=meta.pop("year", 2024),
            doi=meta.pop("doi", "https://doi.org/10.1/x.y"),
            **meta,
        ),
        markdown_path=f"corpus/markdown/{doc_id}.md",
        image_dir=f"corpus/images/{doc_id}/",
        abstract="A short abstract.",
        n_chunks=2, n_tokens=42,
    )


def _make_chunks(doc_id: str = "d1", n: int = 2) -> list[Chunk]:
    return [
        Chunk(
            id=f"{doc_id}/c{i}", doc_id=doc_id, ord=i,
            text=f"text body {i} mentioning titanium dioxide",
            char_span=(i * 10, i * 10 + 9), section_path=["Intro"],
            section_type="intro", equation_ids=[f"eq{i}"] if i == 0 else [],
        )
        for i in range(n)
    ]


def test_document_upsert_and_doi_normalization():
    s = Store(":memory:")
    d = _make_doc(doi="HTTPS://DOI.ORG/10.1234/Foo")
    s.upsert_document(d)
    row = s.get_document("d1")
    assert row["doi"] == "10.1234/foo"
    assert row["title"] == "Sample title"
    assert row["abstract"] == "A short abstract."
    assert row["year"] == 2024


def test_chunk_upsert_round_trip_preserves_equation_ids():
    s = Store(":memory:")
    s.upsert_document(_make_doc())
    s.upsert_chunks(_make_chunks())
    chunks = s.get_chunks("d1")
    assert [c["chunk_id"] for c in chunks] == ["d1/c0", "d1/c1"]
    import json
    assert json.loads(chunks[0]["equation_ids_json"]) == ["eq0"]
    assert chunks[0]["section_type"] == "intro"
    assert chunks[0]["char_start"] == 0


def test_authors_upsert_and_coauthor_edges():
    s = Store(":memory:")
    s.upsert_document(_make_doc())
    ids = s.upsert_document_authors("d1", ["Jane Doe", "Alex Roe"])
    assert sorted(ids) == sorted(["jane doe", "alex roe"])
    rows = s.get_authors("d1")
    assert [r["display_name"] for r in rows] == ["Jane Doe", "Alex Roe"]
    edges = list(s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='coauthor'",
    ))
    assert len(edges) == 1
    a, b = edges[0]
    assert a < b


def test_fk_cascade_on_delete_document():
    s = Store(":memory:")
    d1 = _make_doc("d1")
    d2 = _make_doc("d2", doi="https://doi.org/10.2/q")
    s.upsert_document(d1)
    s.upsert_document(d2)
    s.upsert_chunks(_make_chunks("d1"))
    s.upsert_chunks(_make_chunks("d2"))
    s.upsert_document_authors("d1", ["Jane Doe"])
    s.upsert_document_authors("d2", ["Jane Doe"])
    s.upsert_bib_entries("d1", [
        {"raw_text": "ref 1", "title": "Sample title", "year": 2024,
         "doi": "https://doi.org/10.2/q"},
    ])
    # outbound graph edge from d1 to d2 stays only after re-resolve
    s.reresolve_inbound("d2")
    s.refresh_reference_edges("d1")
    pre_edges = s.con.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE src_type='document' AND src_id='d1'",
    ).fetchone()[0]
    assert pre_edges >= 1

    s.delete_document("d1")
    # Chunks gone via FK
    assert s.con.execute("SELECT COUNT(*) FROM chunks WHERE doc_id='d1'").fetchone()[0] == 0
    # bib_entries gone via FK
    assert s.con.execute("SELECT COUNT(*) FROM bib_entries WHERE doc_id='d1'").fetchone()[0] == 0
    # graph_edges originating from d1 gone
    assert s.con.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE src_type='document' AND src_id='d1'",
    ).fetchone()[0] == 0
    # d2 untouched
    assert s.get_document("d2") is not None
    assert len(s.get_chunks("d2")) == 2


def test_sync_rebuilds_coauthor_edges_when_only_link_doc_dropped():
    """Reviewer scenario: A+B coauthor on d1; A on d2; B on d3. Removing
    d1 must wipe the A-B coauthor edge — it's no longer asserted by any
    surviving doc, even though both authors are still canonical rows."""
    from wikify.corpus.store.sync import _sync_remove_absent_docs

    s = Store(":memory:")
    for did, authors in [
        ("d1", ["Alice X", "Bob Y"]),
        ("d2", ["Alice X"]),
        ("d3", ["Bob Y"]),
    ]:
        s.upsert_document(_make_doc(did, authors=authors))
        s.upsert_document_authors(did, authors)

    pre = list(s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='coauthor'",
    ))
    assert len(pre) == 1, "expected one A-B coauthor edge from d1"

    _sync_remove_absent_docs(s, {"d2", "d3"})

    post = list(s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='coauthor'",
    ))
    assert post == [], f"stale A-B coauthor edge survived: {post}"
    # Both authors are still canonical rows, just not coauthors anymore.
    assert s.con.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 2
