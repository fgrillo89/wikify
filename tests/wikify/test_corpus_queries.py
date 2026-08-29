"""Tests for wikify.corpus.queries — handle parsing + corpus listings.

Most tests build a SQLite-only corpus via the ``make_sqlite_corpus``
fixture so they don't pull in the embedding stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Corpus
from wikify.corpus import queries
from wikify.corpus.chunks import (
    all_chunks as storage_all_chunks,
)
from wikify.corpus.chunks import (
    list_documents as storage_list_documents,
)
from wikify.corpus.chunks import (
    read_chunks as storage_read_chunks,
)
from wikify.corpus.chunks import (
    read_chunks_by_id,
)
from wikify.models import Chunk, Document

# ----------------------------------------------------------- handle parsing


def test_parse_handle_doc() -> None:
    assert queries.parse_handle("doc:paper_A") == ("doc", "paper_A")


def test_parse_handle_chunk_with_colons() -> None:
    """The id portion may itself contain colons; only the first split."""
    assert queries.parse_handle("chunk:paper_A__c0001:abc") == (
        "chunk",
        "paper_A__c0001:abc",
    )


def test_parse_handle_rejects_no_colon() -> None:
    with pytest.raises(ValueError, match="kind:id"):
        queries.parse_handle("paper_A")


# ----------------------------------------------------------- corpus on disk


def _build_docs(n_docs: int) -> list[tuple[Document, list[Chunk]]]:
    out: list[tuple[Document, list[Chunk]]] = []
    for i in range(n_docs):
        doc_id = f"paper_{i}"
        doc = Document(
            id=doc_id,
            source_path=f"src/{doc_id}.md",
            kind="md",
            title=f"Title {i}",
            metadata={"year": 2020 + i, "authors": [f"author_{i}"]},
            markdown_path=f"markdown/{doc_id}.md",
            image_dir=f"images/{doc_id}/",
            n_chunks=2,
            n_tokens=50,
        )
        chunks = [
            Chunk(
                id=f"{doc_id}__c{j:04d}",
                doc_id=doc_id,
                ord=j,
                text=(
                    f"Chunk {j} of paper {i} talks about atomic layer deposition."
                ),
                char_span=(j * 100, j * 100 + 60),
                section_path=["intro"] if j == 0 else ["body"],
                section_type="body",
            )
            for j in range(2)
        ]
        out.append((doc, chunks))
    return out


def _make_corpus(root: Path, n_docs: int = 2) -> Corpus:
    """Build a SQLite-only corpus rooted at *root* for cross-test reuse.

    Mirrors the previous on-disk fixture's contract (markdown sidecars,
    a stub manifest, and ``n_docs`` docs each with two chunks of
    "atomic layer deposition" text), but persists everything through
    ``project_documents`` so the test corpus matches what ingest
    produces.
    """
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents

    docs_chunks = _build_docs(n_docs)
    corpus = Corpus(root=root)
    corpus.ensure()
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(
                store,
                [d for d, _ in docs_chunks],
                {d.id: list(ch) for d, ch in docs_chunks},
            )
        store.fts_rebuild()
    finally:
        store.close()
    for doc, _ in docs_chunks:
        (corpus.markdown_dir / f"{doc.id}.md").write_text(
            f"# {doc.title}\n\nSome body text.\n", encoding="utf-8",
        )
    corpus.manifest_path.write_text("{}", encoding="utf-8")
    return corpus


def _make_sqlite_only_corpus(root: Path, n_docs: int = 2) -> Corpus:
    """Backwards-compatible alias for callers that want a SQLite-only corpus.

    The schema is now SQLite-only by default, so this delegates to
    ``_make_corpus``.
    """
    return _make_corpus(root, n_docs=n_docs)


@pytest.fixture
def small_corpus(make_sqlite_corpus):
    corpus = make_sqlite_corpus(_build_docs(2))
    (corpus.markdown_dir / "paper_0.md").write_text(
        "# Title 0\n\nSome body text.\n", encoding="utf-8",
    )
    (corpus.markdown_dir / "paper_1.md").write_text(
        "# Title 1\n\nSome body text.\n", encoding="utf-8",
    )
    corpus.manifest_path.write_text("{}", encoding="utf-8")
    return corpus


def test_list_doc_ids(make_sqlite_corpus) -> None:
    corpus = make_sqlite_corpus(_build_docs(3))
    ids = queries.list_doc_ids(corpus)
    assert ids == ["paper_0", "paper_1", "paper_2"]


def test_list_chunks_for_doc(small_corpus: Corpus) -> None:
    chunks = queries.list_chunks_for_doc(small_corpus, "paper_0")
    assert [c.id for c in chunks] == ["paper_0__c0000", "paper_0__c0001"]


def test_list_chunks_unknown_doc(small_corpus: Corpus) -> None:
    assert queries.list_chunks_for_doc(small_corpus, "no_such_doc") == []


def test_list_files(small_corpus: Corpus) -> None:
    files = queries.list_files(small_corpus)
    assert any("markdown/paper_0.md" in f.replace("\\", "/") for f in files)
    assert any("manifest.json" in f for f in files)


def test_get_doc_known(small_corpus: Corpus) -> None:
    doc = queries.get_doc(small_corpus, "paper_0")
    assert doc is not None
    assert doc.title == "Title 0"
    assert doc.metadata["year"] == 2020


def test_get_doc_unknown(small_corpus: Corpus) -> None:
    assert queries.get_doc(small_corpus, "missing") is None


def test_get_chunk_known(small_corpus: Corpus) -> None:
    chunk = queries.get_chunk(small_corpus, "paper_0__c0001")
    assert chunk is not None
    assert chunk.doc_id == "paper_0"
    assert "atomic layer deposition" in chunk.text


def test_get_chunk_unknown(small_corpus: Corpus) -> None:
    assert queries.get_chunk(small_corpus, "no_such_chunk") is None


def test_search_text_substring(small_corpus: Corpus) -> None:
    hits = queries.search_text(small_corpus, "atomic layer", top_k=10)
    assert len(hits) == 4  # 2 docs * 2 chunks
    for h in hits:
        assert "id" in h and "doc_id" in h and "preview" in h


def test_search_text_case_insensitive(small_corpus: Corpus) -> None:
    hits = queries.search_text(small_corpus, "ATOMIC LAYER", top_k=10)
    assert len(hits) == 4


def test_search_text_no_matches(small_corpus: Corpus) -> None:
    assert queries.search_text(small_corpus, "memristor switching", top_k=10) == []


def test_check_corpus_basic(small_corpus: Corpus) -> None:
    summary = queries.check_corpus(small_corpus)
    assert summary["n_docs"] == 2
    assert summary["n_chunks"] == 4
    assert summary["has_manifest"] is True
    assert summary["has_vectors"] is False
    assert summary["has_sqlite_store"] is True
    assert summary["fingerprint"] == small_corpus.manifest_fingerprint()
    assert len(summary["fingerprint"]) == 16


def test_degenerate_ranking_metric_is_reported(make_sqlite_corpus) -> None:
    """With an unresolved citation graph every document keeps the same
    baseline PageRank, so "top-K by PageRank" silently degrades to alphabetical
    order and surfaces off-topic papers as the corpus's most central work."""
    from wikify.corpus.store.routing import open_store

    corpus = make_sqlite_corpus(_build_docs(12))
    store = open_store(corpus.root)
    try:
        doc_ids = [r[0] for r in store.con.execute("SELECT doc_id FROM documents")]
        store.con.executemany(
            "INSERT OR REPLACE INTO node_metrics"
            "(graph_name, node_type, node_id, metric, value, computed_at) "
            "VALUES ('corpus_citation', 'document', ?, ?, ?, '2026-01-01T00:00:00Z')",
            [(d, "pagerank", 0.0833333) for d in doc_ids]
            # A healthy metric on the same nodes must NOT be flagged.
            + [(d, "citation_count", float(i)) for i, d in enumerate(doc_ids)]
            # ...nor a tied metric that no caller can ORDER BY. Several stored
            # metrics are constant by construction (a chunk's in_degree is
            # always 1 — it belongs to one document), so checking every metric
            # would bury the one signal that matters under permanent noise.
            + [(d, "in_degree", 1.0) for d in doc_ids],
        )
        store.con.commit()
        flagged = queries.degenerate_ranking_metrics(store.con)
    finally:
        store.close()

    by_metric = {d["metric"]: d for d in flagged}
    assert set(by_metric) == {"pagerank"}
    assert by_metric["pagerank"]["n_nodes"] == 12
    assert by_metric["pagerank"]["n_distinct_values"] == 1
    assert by_metric["pagerank"]["tied_share"] == 1.0
    assert by_metric["pagerank"]["n_ranked_above_tie"] == 0
    assert by_metric["pagerank"]["node_type"] == "document"

    assert queries.check_corpus(corpus)["degenerate_metrics"] == flagged


def test_degenerate_head_threshold_matches_the_documented_ceiling() -> None:
    """Pin `_DEGENERATE_MIN_DISTINGUISHED` to the largest top-K a caller takes.

    Nothing pinned it before, so changing it left the whole suite green while
    the agent-facing reference kept quoting the old number. The constant is
    only meaningful as "the biggest wave a caller can fill", so it is asserted
    against the real ceilings rather than against a literal.
    """
    import re

    from wikify.cli.run import _sizing_knobs

    # `wave_size` is the widest top-K any caller takes; `corpus find --top-k`
    # defaults lower still.
    widest_wave = max(
        _sizing_knobs(n_docs, n_chunks=0)["wave_size"]
        for n_docs in (1, 100, 1_000, 100_000)
    )
    assert queries._DEGENERATE_MIN_DISTINGUISHED == widest_wave

    # ...and the reference doc must quote the same number.
    doc = (
        Path(__file__).resolve().parents[2]
        / ".claude/skills/wikify/subskills/search-corpus/references"
        / "corpus-cli-patterns.md"
    ).read_text(encoding="utf-8")
    quoted = {int(n) for n in re.findall(r"fewer than (\d+) (?:nodes|documents)", doc)}
    assert quoted, "the reference doc no longer quotes the threshold"
    assert quoted == {widest_wave}, quoted


def test_a_well_spread_head_is_not_degenerate(make_sqlite_corpus) -> None:
    """What rescues a metric is a HEAD a top-K can fill, not the position of
    its tied block. `refresh_citation_count` writes a row per document via
    COALESCE(..., 0), so most documents sit at 0; that huge bottom block is
    harmless as long as enough documents outrank it. Flagging it anyway would
    steer the editor off citation seeding on a healthy corpus."""
    from wikify.corpus.store.routing import open_store

    corpus = make_sqlite_corpus(_build_docs(2))
    store = open_store(corpus.root)
    try:
        # 216 docs: 200 tied at 0.0 (92.6% — comfortably over the share
        # threshold) but 16 with distinct nonzero counts above them.
        values = [0.0] * 200 + [float(i + 1) for i in range(16)]
        store.con.executemany(
            "INSERT OR REPLACE INTO node_metrics"
            "(graph_name, node_type, node_id, metric, value, computed_at) "
            "VALUES ('corpus_citation', 'document', ?, 'citation_count', ?, 'x')",
            [(f"s{i}", v) for i, v in enumerate(values)],
        )
        store.con.commit()
        flagged = queries.degenerate_ranking_metrics(store.con)
    finally:
        store.close()
    assert flagged == []

    # ...and the SAME shape with too thin a head IS flagged: a top-K there is
    # mostly alphabetical padding, which is the whole point of the check.
    store = open_store(corpus.root)
    try:
        store.con.execute(
            "DELETE FROM node_metrics WHERE metric='citation_count' AND value > 2.0"
        )
        store.con.commit()
        thin = queries.degenerate_ranking_metrics(store.con)
    finally:
        store.close()
    assert [d["metric"] for d in thin] == ["citation_count"]
    assert thin[0]["n_ranked_above_tie"] == 2


def test_partially_resolved_ranking_is_still_degenerate(make_sqlite_corpus) -> None:
    """One resolved citation among 200 documents leaves 199 tied on the
    baseline. Measuring ties only at the MAX would miss it, yet a top-K still
    exhausts the single distinguished node and pads the rest alphabetically."""
    from wikify.corpus.store.routing import open_store

    corpus = make_sqlite_corpus(_build_docs(2))
    store = open_store(corpus.root)
    try:
        values = [0.9] + [0.004] * 199
        store.con.executemany(
            "INSERT OR REPLACE INTO node_metrics"
            "(graph_name, node_type, node_id, metric, value, computed_at) "
            "VALUES ('corpus_citation', 'document', ?, 'pagerank', ?, 'x')",
            [(f"s{i}", v) for i, v in enumerate(values)],
        )
        store.con.commit()
        flagged = queries.degenerate_ranking_metrics(store.con)
    finally:
        store.close()
    assert len(flagged) == 1
    assert flagged[0]["metric"] == "pagerank"
    assert flagged[0]["n_distinct_values"] == 2
    assert flagged[0]["tied_share"] == 0.995
    assert flagged[0]["n_ranked_above_tie"] == 1


def test_text_defect_counts_flag_unquotable_chunks(make_sqlite_corpus) -> None:
    """A dropped ligature leaves a control char mid-word (`di\\x0fusivity`)
    and a soft hyphen splits an ordinary word (`real\\xadworld`). A writer
    retypes the word as rendered, so the span can never ground — report the
    scale before dispatching writers."""
    from wikify.corpus.store.routing import open_store

    corpus = make_sqlite_corpus(_build_docs(2))
    store = open_store(corpus.root)
    try:
        ids = [r[0] for r in store.con.execute(
            "SELECT chunk_id FROM chunks ORDER BY chunk_id"
        )]
        store.con.execute(
            "UPDATE chunks SET text = 'measured the di\x0fusivity directly' "
            "WHERE chunk_id = ?", (ids[0],)
        )
        store.con.execute(
            "UPDATE chunks SET text = 'a real­world deployment' "
            "WHERE chunk_id = ?", (ids[1],)
        )
        store.con.commit()
    finally:
        store.close()

    defects = queries.check_corpus(corpus, full=True)["text_defects"]
    assert defects == {
        "chunks_with_control_chars": 1,
        "chunks_with_soft_hyphens": 1,
    }
    # The cheap default form does not pay for the scan.
    assert "text_defects" not in queries.check_corpus(corpus)


def test_storage_reads_documents_from_sqlite(make_sqlite_corpus) -> None:
    corpus = make_sqlite_corpus(_build_docs(3))

    docs = storage_list_documents(corpus)

    assert [doc.id for doc in docs] == ["paper_0", "paper_1", "paper_2"]
    assert docs[0].title == "Title 0"
    assert docs[0].metadata["year"] == 2020


def test_storage_reads_chunks_from_sqlite(make_sqlite_corpus) -> None:
    corpus = make_sqlite_corpus(_build_docs(2))

    doc_chunks = storage_read_chunks(corpus, "paper_0")
    all_doc_chunks = storage_all_chunks(corpus)
    selected = read_chunks_by_id(
        corpus,
        ["paper_1__c0001", "paper_0__c0000", "missing"],
    )
    limited = read_chunks_by_id(
        corpus,
        ["paper_1__c0001", "paper_0__c0000"],
        limit=1,
    )

    assert [c.id for c in doc_chunks] == ["paper_0__c0000", "paper_0__c0001"]
    assert [c.id for c in all_doc_chunks] == [
        "paper_0__c0000",
        "paper_0__c0001",
        "paper_1__c0000",
        "paper_1__c0001",
    ]
    assert [c.id for c in selected] == ["paper_1__c0001", "paper_0__c0000"]
    assert [c.id for c in limited] == ["paper_1__c0001"]


def test_queries_use_sqlite(small_corpus: Corpus) -> None:
    assert queries.list_doc_ids(small_corpus) == ["paper_0", "paper_1"]
    assert queries.get_doc(small_corpus, "paper_0").title == "Title 0"
    assert queries.get_chunk(small_corpus, "paper_0__c0001").doc_id == "paper_0"
    assert [c.id for c in queries.list_chunks_for_doc(small_corpus, "paper_0")] == [
        "paper_0__c0000",
        "paper_0__c0001",
    ]

    text_hits = queries.search_text(small_corpus, "atomic layer", top_k=10)
    assert [hit["id"] for hit in text_hits] == [
        "paper_0__c0000",
        "paper_0__c0001",
        "paper_1__c0000",
        "paper_1__c0001",
    ]

    found = queries.find(
        small_corpus,
        query="atomic layer",
        by="chunk",
        rank="semantic",
        top_k=4,
        text=True,
    )
    assert found["kind"] == "chunks"
    assert [row["id"] for row in found["rows"]] == [hit["id"] for hit in text_hits]

    body = queries.read_doc_text(small_corpus, "paper_0")
    assert [s["section_path"] for s in body["segments"]] == [["intro"], ["body"]]
    assert queries.doc_section_index(small_corpus, "paper_0")[0]["section_path"] == ["intro"]


# --------------------------------------------------- find/traverse/show orchestrators


def test_schema_describes_relations_and_metrics() -> None:
    """The promoted SCHEMA dict carries every advertised primitive."""
    s = queries.SCHEMA
    assert "doc" in s["traverse_relations"]
    assert "chunk" in s["traverse_relations"]
    assert "author" in s["traverse_relations"]
    assert "citation_count" in s["rank_metrics"]["source"]
    assert "h_index" in s["rank_metrics"]["author"]
    assert "diverse" in s["sample_strategies"]


def test_find_text_chunks(small_corpus: Corpus) -> None:
    result = queries.find(
        small_corpus, query="atomic layer", by="chunk", rank="semantic",
        top_k=5, text=True,
    )
    assert result["kind"] == "chunks"
    assert result["scored"] is False
    assert len(result["rows"]) == 4


def test_find_rejects_chunk_with_metric_rank(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="x", by="chunk", rank="citation_count", top_k=5,
        )
    assert exc.value.code == "bad_rank_by_combo"


def test_find_rejects_paper_with_h_index_rank(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="x", by="paper", rank="h_index", top_k=5,
        )
    assert exc.value.code == "bad_rank_by_combo"


def test_find_rejects_unknown_rank(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="x", by="paper", rank="nonsense", top_k=5,
        )
    assert exc.value.code == "bad_rank"


def test_find_rejects_missing_query(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="", by="chunk", rank="semantic", top_k=5,
        )
    assert exc.value.code == "missing_query"


def test_find_queryless_pagerank_returns_docs_ordered_by_pr(
    small_corpus: Corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Query-less --rank pagerank returns kind='docs' ordered by descending pagerank."""
    _all_rows = [
        {"doc_id": "paper_0", "title": "Title 0", "citation_count": 0, "pagerank": 0.05},
        {"doc_id": "paper_1", "title": "Title 1", "citation_count": 0, "pagerank": 0.20},
    ]

    def _fake_rank_docs(_corpus, *, by, top_k):
        rows = sorted(
            _all_rows,
            key=lambda r: -r[by] if by == "pagerank" else (-r[by], r["doc_id"]),
        )
        return rows[:top_k]

    monkeypatch.setattr(queries, "rank_docs", _fake_rank_docs)

    # by="paper" routes to the document metric-ranking path.
    result = queries.find(
        small_corpus, query="", by="paper", rank="pagerank", top_k=5,
    )
    assert result["kind"] == "docs"
    assert result["scored"] is False
    pr_list = [r["pagerank"] for r in result["rows"]]
    assert pr_list == sorted(pr_list, reverse=True), "rows not ordered by pagerank desc"
    assert len(result["rows"]) == 2

    # Explicit --by paper — same path, top_k=1 returns highest-pr doc.
    result_paper = queries.find(
        small_corpus, query="", by="paper", rank="pagerank", top_k=1,
    )
    assert result_paper["kind"] == "docs"
    assert len(result_paper["rows"]) == 1
    assert result_paper["rows"][0]["pagerank"] == 0.20


def test_find_queryless_citation_count_returns_docs_ordered_by_cites(
    small_corpus: Corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Query-less --rank citation_count returns kind='docs' ordered by descending cites."""
    _all_rows = [
        {"doc_id": "paper_0", "title": "Title 0", "citation_count": 42, "pagerank": 0.0},
        {"doc_id": "paper_1", "title": "Title 1", "citation_count": 7, "pagerank": 0.0},
    ]

    def _fake_rank_docs(_corpus, *, by, top_k):
        rows = sorted(_all_rows, key=lambda r: (-r[by], r["doc_id"]))
        return rows[:top_k]

    monkeypatch.setattr(queries, "rank_docs", _fake_rank_docs)

    result = queries.find(
        small_corpus, query="", by="paper", rank="citation_count", top_k=5,
    )
    assert result["kind"] == "docs"
    cites_list = [r["citation_count"] for r in result["rows"]]
    assert cites_list == sorted(cites_list, reverse=True)


def test_find_with_query_and_metric_rank_still_rejects_chunk_by(
    small_corpus: Corpus,
) -> None:
    """When a query is present, --by chunk --rank pagerank must still be rejected."""
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="something", by="chunk", rank="pagerank", top_k=5,
        )
    assert exc.value.code == "bad_rank_by_combo"


def test_find_rejects_zero_top_k(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="x", by="chunk", rank="semantic", top_k=0,
        )
    assert exc.value.code == "bad_top_k"


def test_show_doc(small_corpus: Corpus) -> None:
    result = queries.show(small_corpus, handle="doc:paper_0")
    assert result["handle_kind"] == "doc"
    assert result["data"].title == "Title 0"


def test_show_chunk_full_flag(small_corpus: Corpus) -> None:
    result = queries.show(
        small_corpus, handle="chunk:paper_0__c0000", full=True,
    )
    assert result["handle_kind"] == "chunk"
    assert result["full"] is True


def test_show_doc_not_found(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.show(small_corpus, handle="doc:nope")
    assert exc.value.code == "doc_not_found"


def test_show_bad_handle_format(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.show(small_corpus, handle="no_colon_here")
    assert exc.value.code == "bad_handle"


def test_traverse_rejects_bad_relation(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.traverse(small_corpus, handle="doc:paper_0", to="nonsense")
    assert exc.value.code == "bad_relation"


def test_traverse_rejects_bad_handle_kind(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.traverse(small_corpus, handle="figure:foo/bar", to="chunks")
    assert exc.value.code == "bad_handle_kind"


def test_traverse_doc_chunks_returns_document_order_with_section(
    small_corpus: Corpus,
) -> None:
    """Doc -> chunks must be ordered by ``ord`` and carry section_path."""
    rows = queries.traverse_doc(
        small_corpus, doc_id="paper_0", relation="chunks",
    )
    assert [r["ord"] for r in rows] == sorted(r["ord"] for r in rows)
    assert all("section_path" in r for r in rows)


def test_search_papers_by_title_is_case_insensitive_substring(
    small_corpus: Corpus,
) -> None:
    rows = queries.search_papers_by_title(small_corpus, "title 1", top_k=5)
    assert [r["doc_id"] for r in rows] == ["paper_1"]


def test_find_field_title_dispatches_to_title_search(small_corpus: Corpus) -> None:
    result = queries.find(
        small_corpus, query="title 0", by="paper", rank="semantic",
        top_k=5, field="title",
    )
    assert result["kind"] == "papers"
    assert result["scored"] is False
    assert [r["doc_id"] for r in result["rows"]] == ["paper_0"]


def test_find_field_title_honors_source_metric_rank(
    small_corpus: Corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_doc_metrics(_corpus, doc_ids):
        return {
            did: {
                "citation_count": 100 if did == "paper_1" else 1,
                "pagerank": 0.0,
            }
            for did in doc_ids
        }

    monkeypatch.setattr(queries, "doc_metrics", _fake_doc_metrics)
    result = queries.find(
        small_corpus, query="title", by="paper", rank="citation_count",
        top_k=1, field="title",
    )
    assert [r["doc_id"] for r in result["rows"]] == ["paper_1"]
    assert result["rows"][0]["citation_count"] == 100


def test_find_field_title_rejects_non_paper_by(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="x", by="chunk", rank="semantic",
            top_k=5, field="title",
        )
    assert exc.value.code == "bad_field_by_combo"


def test_find_field_title_rejects_empty_query(small_corpus: Corpus) -> None:
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            small_corpus, query="", by="paper", rank="semantic",
            top_k=5, field="title",
        )
    assert exc.value.code == "missing_query"


def test_read_doc_text_groups_consecutive_chunks_by_section(
    small_corpus: Corpus,
) -> None:
    out = queries.read_doc_text(small_corpus, "paper_0")
    segments = out["segments"]
    assert [s["section_path"] for s in segments] == [["intro"], ["body"]]
    assert all("text" in s and "chunk_ids" in s for s in segments)
    assert out["available_section_paths"] == [["intro"], ["body"]]


def test_read_doc_text_section_filter(small_corpus: Corpus) -> None:
    out = queries.read_doc_text(small_corpus, "paper_0", sections=["intro"])
    assert [s["section_path"] for s in out["segments"]] == [["intro"]]
    assert out["matched_section_paths"] == [["intro"]]


def test_read_doc_text_section_filter_tolerates_numbering(
    make_sqlite_corpus,
) -> None:
    """Filter must hit ``"V. SUMMARY"``-style headings from a token like 'summary'."""
    doc = Document(
        id="p", source_path="src/p.md", kind="md", title="P",
        metadata={}, markdown_path="markdown/p.md",
        image_dir="images/p/", n_chunks=2, n_tokens=50,
    )
    chunks = [
        Chunk(
            id="p__c0", doc_id="p", ord=0, text="intro text",
            char_span=(0, 10), section_path=["I. INTRODUCTION"],
            section_type="body",
        ),
        Chunk(
            id="p__c1", doc_id="p", ord=1, text="summary text",
            char_span=(10, 22), section_path=["V. SUMMARY"],
            section_type="body",
        ),
    ]
    corpus = make_sqlite_corpus([(doc, chunks)])
    (corpus.markdown_dir / "p.md").write_text("# P", encoding="utf-8")
    corpus.manifest_path.write_text("{}", encoding="utf-8")

    out = queries.read_doc_text(corpus, "p", sections=["summary"])
    assert [s["section_path"] for s in out["segments"]] == [["V. SUMMARY"]]
    out_intro = queries.read_doc_text(corpus, "p", sections=["introduction"])
    assert [s["section_path"] for s in out_intro["segments"]] == [["I. INTRODUCTION"]]


def test_read_doc_text_skips_image_caption_chunks(make_sqlite_corpus) -> None:
    """Figure-caption chunks (``__image__`` section) must not appear in body text."""
    doc = Document(
        id="p", source_path="src/p.md", kind="md", title="P",
        metadata={}, markdown_path="markdown/p.md",
        image_dir="images/p/", n_chunks=2, n_tokens=50,
    )
    chunks = [
        Chunk(
            id="p__c0", doc_id="p", ord=0, text="real body",
            char_span=(0, 9), section_path=["Body"],
            section_type="body",
        ),
        Chunk(
            id="p__c1", doc_id="p", ord=1, text="Figure 1: caption stub",
            char_span=(9, 30), section_path=["__image__"],
            section_type="body",
        ),
    ]
    corpus = make_sqlite_corpus([(doc, chunks)])
    (corpus.markdown_dir / "p.md").write_text("# P", encoding="utf-8")
    corpus.manifest_path.write_text("{}", encoding="utf-8")

    out = queries.read_doc_text(corpus, "p")
    assert [s["section_path"] for s in out["segments"]] == [["Body"]]
    assert ["__image__"] not in out["available_section_paths"]


def test_doc_section_index(small_corpus: Corpus) -> None:
    idx = queries.doc_section_index(small_corpus, "paper_0")
    paths = [s["section_path"] for s in idx]
    assert ["intro"] in paths
    assert ["body"] in paths
    assert all(s["n_chunks"] >= 1 for s in idx)
