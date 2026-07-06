"""Duplicate-document collapse: same DOI (or title) folds onto one canonical
id and the duplicates' citations are redirected, not lost."""

from __future__ import annotations

from wikify.corpus.dedup import apply_dedup, plan_dedup
from wikify.corpus.store import Store
from wikify.corpus.store.graph import Edge, GraphStore
from wikify.models import Chunk, Document


def _doc(doc_id: str, doi: str | None, title: str) -> Document:
    return Document(
        id=doc_id, source_path=f"src/{doc_id}.pdf", kind="pdf", title=title,
        metadata={"doi": doi} if doi else {},
        markdown_path=f"markdown/{doc_id}.md", image_dir=f"images/{doc_id}/",
        n_chunks=1, n_tokens=10,
    )


def _chunk(doc_id: str, n: int = 1) -> list[Chunk]:
    return [
        Chunk(id=f"{doc_id}__c{i:04d}", doc_id=doc_id, ord=i, text=f"body {i}",
              char_span=(0, 6), section_path=["S"], section_type="body")
        for i in range(n)
    ]


def _edge(src: str, dst: str) -> Edge:
    return Edge("document", src, "references", "document", dst)


def test_dedup_folds_duplicate_and_redirects_citation(make_sqlite_corpus) -> None:
    # canonical (clean name, 2 chunks), a mangled 8.3 duplicate sharing the
    # DOI, and a third doc that cites the DUPLICATE.
    corpus = make_sqlite_corpus([
        (_doc("[Real2020] A paper", "10.1/x", "A paper"), _chunk("[Real2020] A paper", 2)),
        (_doc("_REAL~1", "10.1/x", "A paper"), _chunk("_REAL~1", 1)),
        (_doc("[Citer2021] Other", "10.2/y", "Other"), _chunk("[Citer2021] Other", 1)),
    ])
    store = Store(corpus.sqlite_path)
    try:
        GraphStore(store.con).upsert_edges([_edge("[Citer2021] Other", "_REAL~1")])
        store.con.commit()
    finally:
        store.close()

    plan = apply_dedup(corpus)

    assert plan["removed"] == ["_REAL~1"]
    store = Store(corpus.sqlite_path)
    try:
        ids = {r[0] for r in store.con.execute("SELECT doc_id FROM documents")}
        assert ids == {"[Real2020] A paper", "[Citer2021] Other"}
        # The citation that pointed at the duplicate now points at canonical.
        edges = [tuple(r) for r in store.con.execute(
            "SELECT src_id, dst_id FROM graph_edges WHERE kind='references'"
        )]
        assert edges == [("[Citer2021] Other", "[Real2020] A paper")]
    finally:
        store.close()


def test_dedup_plan_prefers_nonmangled_then_more_chunks(make_sqlite_corpus) -> None:
    corpus = make_sqlite_corpus([
        (_doc("_MANG~1", "10.1/z", "Same"), _chunk("_MANG~1", 5)),
        (_doc("[Clean2019] Same", "10.1/z", "Same"), _chunk("[Clean2019] Same", 1)),
    ])
    store = Store(corpus.sqlite_path)
    try:
        plan = plan_dedup(store)
    finally:
        store.close()
    assert len(plan) == 1
    # Non-mangled wins even though the mangled copy has more chunks.
    assert plan[0]["canonical"] == "[Clean2019] Same"
    assert plan[0]["duplicates"] == ["_MANG~1"]


def test_dedup_title_fallback_when_no_doi(make_sqlite_corpus) -> None:
    corpus = make_sqlite_corpus([
        (_doc("doc-a", None, "Identical Title"), _chunk("doc-a", 2)),
        (_doc("doc-b", None, "Identical  Title!"), _chunk("doc-b", 1)),
    ])
    store = Store(corpus.sqlite_path)
    try:
        plan = plan_dedup(store)
    finally:
        store.close()
    assert len(plan) == 1
    assert set([plan[0]["canonical"], *plan[0]["duplicates"]]) == {"doc-a", "doc-b"}
