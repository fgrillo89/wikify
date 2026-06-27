"""Phase 4 acceptance: wiki.db projection on commit + empty-wiki path."""

from __future__ import annotations

from pathlib import Path

from wikify.bundle.wiki.queries import find_bm25, find_text
from wikify.bundle.wiki.store import (
    list_wiki_pages,
    open_wiki_store,
    search_wiki_bm25,
    upsert_wiki_page,
)


def _make_bundle(root: Path):
    """Construct a minimal Bundle-like object for store-only tests."""
    class _B:
        def __init__(self):
            self.root = root
            self.sqlite_path = root / "wiki.db"
    root.mkdir(parents=True, exist_ok=True)
    return _B()


def test_empty_wiki_find_bm25_returns_empty(tmp_path):
    bundle = _make_bundle(tmp_path / "bundle")
    assert find_bm25(bundle, "anything", top_k=10) == []


def test_upsert_and_search_wiki_page(tmp_path):
    db = tmp_path / "wiki.db"
    con = open_wiki_store(db)
    try:
        upsert_wiki_page(
            con,
            page_id="Atomic layer deposition",
            slug="atomic-layer-deposition",
            title="Atomic layer deposition",
            kind="article",
            body="Atomic layer deposition is a thin-film technique used for "
                 "conformal coatings on high aspect ratio substrates.",
            evidence=[
                {"chunk_id": "doc1/c0", "doc_id": "doc1", "marker": "e1"},
            ],
            links=["Photocatalysis"],
        )
        upsert_wiki_page(
            con,
            page_id="Photocatalysis",
            slug="photocatalysis",
            title="Photocatalysis",
            kind="article",
            body="Photocatalysis on titanium dioxide drives water splitting.",
        )
        ids = sorted(p["page_id"] for p in list_wiki_pages(con))
        assert ids == ["Atomic layer deposition", "Photocatalysis"]

        # FTS5 treats `-` as NOT; quote the phrase so the hyphen is literal.
        hits = search_wiki_bm25(con, '"thin-film"', top_k=5)
        assert hits and hits[0][0] == "Atomic layer deposition"

        edges = list(con.execute("SELECT src_id, kind, dst_id FROM wiki_edges"))
        edge_set = {(r[0], r[1], r[2]) for r in edges}
        assert ("Atomic layer deposition", "cites_evidence", "doc1/c0") in edge_set
        assert ("Atomic layer deposition", "grounded_in", "doc1") in edge_set
        assert ("Atomic layer deposition", "links_to", "Photocatalysis") in edge_set
    finally:
        con.close()


def test_per_page_isolation_on_reupsert(tmp_path):
    """Committing one page must not change another page's row."""
    db = tmp_path / "wiki.db"
    con = open_wiki_store(db)
    try:
        upsert_wiki_page(
            con, page_id="A", slug="a", title="A", kind="article", body="alpha body",
        )
        upsert_wiki_page(
            con, page_id="B", slug="b", title="B", kind="article", body="beta body",
        )
        before_a = dict(con.execute(
            "SELECT * FROM wiki_pages WHERE page_id='A'",
        ).fetchone())

        # Update only B.
        upsert_wiki_page(
            con, page_id="B", slug="b", title="B prime",
            kind="article", body="updated beta body",
        )
        after_a = dict(con.execute(
            "SELECT * FROM wiki_pages WHERE page_id='A'",
        ).fetchone())
        # A's body and timestamps must match exactly.
        assert before_a == after_a
        # B was updated.
        b = con.execute(
            "SELECT title, body FROM wiki_pages WHERE page_id='B'",
        ).fetchone()
        assert b["title"] == "B prime"
        assert b["body"] == "updated beta body"
    finally:
        con.close()


def test_find_text_still_works_on_markdown_path(tmp_path):
    """The markdown-grep find_text path must still work; phase 4 dual-writes."""
    bundle_root = tmp_path / "bundle"
    (bundle_root / "wiki" / "articles").mkdir(parents=True)
    (bundle_root / "wiki" / "articles" / "alpha.md").write_text(
        "# Alpha\n\nthis page mentions atomic layer deposition.\n",
        encoding="utf-8",
    )

    class _B:
        def __init__(self):
            self.root = bundle_root
            self.wiki_dir = bundle_root / "wiki"
            self.wiki_articles_dir = bundle_root / "wiki" / "articles"
            self.wiki_people_dir = bundle_root / "wiki" / "people"
            self.wiki_data_dir = bundle_root / "wiki" / "data"
            self.sqlite_path = bundle_root / "wiki.db"

    out = find_text(_B(), "atomic layer", top_k=5)
    assert out and out[0]["slug"] == "alpha"
