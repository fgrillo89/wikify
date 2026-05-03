"""Tests for wikify.corpus.queries — handle parsing + corpus listings.

Most tests hand-build a minimal corpus on disk (docs/ + chunks/ +
manifest.json) so they don't pull in the embedding stack.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wikify.api import Corpus
from wikify.corpus import queries

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


def _make_corpus(root: Path, n_docs: int = 2) -> Corpus:
    """Hand-build a minimal corpus (no vectors, no graph). Cheap."""
    corpus = Corpus(root=root)
    corpus.ensure()
    for i in range(n_docs):
        doc_id = f"paper_{i}"
        # docs/<id>.json
        doc_payload = {
            "id": doc_id,
            "source_path": f"src/{doc_id}.md",
            "kind": "md",
            "title": f"Title {i}",
            "metadata": {"year": 2020 + i, "authors": [f"author_{i}"]},
            "markdown_path": f"markdown/{doc_id}.md",
            "image_dir": f"images/{doc_id}/",
            "sections": [],
            "images": [],
            "abstract": "",
            "tldr": "",
            "n_chunks": 2,
            "n_tokens": 50,
            "citations": [],
            "equations": [],
            "figure_refs": [],
            "similar_to": [],
            "cites": [],
            "cites_same": [],
        }
        (corpus.docs_dir / f"{doc_id}.json").write_text(
            json.dumps(doc_payload), encoding="utf-8"
        )
        # chunks/<id>.jsonl with two chunks
        chunks = []
        for j in range(2):
            chunks.append(
                json.dumps(
                    {
                        "id": f"{doc_id}__c{j:04d}",
                        "doc_id": doc_id,
                        "ord": j,
                        "text": f"Chunk {j} of paper {i} talks about atomic layer deposition.",
                        "char_span": [j * 100, j * 100 + 60],
                        "section_path": ["intro"] if j == 0 else ["body"],
                        "section_type": "body",
                        "equation_ids": [],
                        "is_boilerplate": False,
                    }
                )
            )
        (corpus.chunks_dir / f"{doc_id}.jsonl").write_text(
            "\n".join(chunks), encoding="utf-8"
        )
        # markdown/<id>.md
        (corpus.markdown_dir / f"{doc_id}.md").write_text(
            f"# Title {i}\n\nSome body text.\n", encoding="utf-8"
        )
    # Minimal manifest
    (corpus.manifest_path).write_text("{}", encoding="utf-8")
    return corpus


def test_list_doc_ids(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c", n_docs=3)
    ids = queries.list_doc_ids(corpus)
    assert ids == ["paper_0", "paper_1", "paper_2"]


def test_list_chunks_for_doc(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    chunks = queries.list_chunks_for_doc(corpus, "paper_0")
    assert [c.id for c in chunks] == ["paper_0__c0000", "paper_0__c0001"]


def test_list_chunks_unknown_doc(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    assert queries.list_chunks_for_doc(corpus, "no_such_doc") == []


def test_list_files(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    files = queries.list_files(corpus)
    # Must include at least the docs json + chunks jsonl + markdown for each
    # doc + the manifest.
    assert any("docs/paper_0.json" in f.replace("\\", "/") for f in files)
    assert any("chunks/paper_0.jsonl" in f.replace("\\", "/") for f in files)
    assert any("manifest.json" in f for f in files)


def test_get_doc_known(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    doc = queries.get_doc(corpus, "paper_0")
    assert doc is not None
    assert doc.title == "Title 0"
    assert doc.metadata["year"] == 2020


def test_get_doc_unknown(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    assert queries.get_doc(corpus, "missing") is None


def test_get_chunk_known(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    chunk = queries.get_chunk(corpus, "paper_0__c0001")
    assert chunk is not None
    assert chunk.doc_id == "paper_0"
    assert "atomic layer deposition" in chunk.text


def test_get_chunk_unknown(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    assert queries.get_chunk(corpus, "no_such_chunk") is None


def test_search_text_substring(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    hits = queries.search_text(corpus, "atomic layer", top_k=10)
    assert len(hits) == 4  # 2 docs * 2 chunks
    for h in hits:
        assert "id" in h and "doc_id" in h and "preview" in h


def test_search_text_case_insensitive(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    hits = queries.search_text(corpus, "ATOMIC LAYER", top_k=10)
    assert len(hits) == 4


def test_search_text_no_matches(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    assert queries.search_text(corpus, "memristor switching", top_k=10) == []


def test_check_corpus_basic(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    summary = queries.check_corpus(corpus)
    assert summary["n_docs"] == 2
    assert summary["n_chunks"] == 4
    assert summary["has_manifest"] is True
    assert summary["has_vectors"] is False
    assert summary["has_sqlite_store"] is False
    assert "has_knowledge_graph" not in summary


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


def test_find_text_chunks(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = queries.find(
        corpus, query="atomic layer", by="chunk", rank="semantic",
        top_k=5, text=True,
    )
    assert result["kind"] == "chunks"
    assert result["scored"] is False
    assert len(result["rows"]) == 4


def test_find_rejects_chunk_with_metric_rank(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="x", by="chunk", rank="citation_count", top_k=5,
        )
    assert exc.value.code == "bad_rank_by_combo"


def test_find_rejects_paper_with_h_index_rank(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="x", by="paper", rank="h_index", top_k=5,
        )
    assert exc.value.code == "bad_rank_by_combo"


def test_find_rejects_unknown_rank(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="x", by="paper", rank="nonsense", top_k=5,
        )
    assert exc.value.code == "bad_rank"


def test_find_rejects_missing_query(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="", by="chunk", rank="semantic", top_k=5,
        )
    assert exc.value.code == "missing_query"


def test_find_rejects_zero_top_k(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="x", by="chunk", rank="semantic", top_k=0,
        )
    assert exc.value.code == "bad_top_k"


def test_show_doc(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = queries.show(corpus, handle="doc:paper_0")
    assert result["handle_kind"] == "doc"
    assert result["data"].title == "Title 0"


def test_show_chunk_full_flag(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = queries.show(
        corpus, handle="chunk:paper_0__c0000", full=True,
    )
    assert result["handle_kind"] == "chunk"
    assert result["full"] is True


def test_show_doc_not_found(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.show(corpus, handle="doc:nope")
    assert exc.value.code == "doc_not_found"


def test_show_bad_handle_format(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.show(corpus, handle="no_colon_here")
    assert exc.value.code == "bad_handle"


def test_traverse_rejects_bad_relation(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.traverse(corpus, handle="doc:paper_0", to="nonsense")
    assert exc.value.code == "bad_relation"


def test_traverse_rejects_bad_handle_kind(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.traverse(corpus, handle="figure:foo/bar", to="chunks")
    assert exc.value.code == "bad_handle_kind"


def test_traverse_doc_chunks_returns_document_order_with_section(
    tmp_path: Path,
) -> None:
    """doc -> chunks must be ordered by ``ord`` and carry section_path."""
    corpus = _make_corpus(tmp_path / "c")
    rows = queries.traverse_doc(
        corpus, doc_id="paper_0", relation="chunks",
    )
    assert [r["ord"] for r in rows] == sorted(r["ord"] for r in rows)
    assert all("section_path" in r for r in rows)


def test_search_papers_by_title_is_case_insensitive_substring(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    rows = queries.search_papers_by_title(corpus, "title 1", top_k=5)
    assert [r["doc_id"] for r in rows] == ["paper_1"]


def test_find_field_title_dispatches_to_title_search(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = queries.find(
        corpus, query="title 0", by="paper", rank="semantic",
        top_k=5, field="title",
    )
    assert result["kind"] == "papers"
    assert result["scored"] is False
    assert [r["doc_id"] for r in result["rows"]] == ["paper_0"]


def test_find_field_title_honors_source_metric_rank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = _make_corpus(tmp_path / "c", n_docs=2)

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
        corpus, query="title", by="paper", rank="citation_count",
        top_k=1, field="title",
    )
    assert [r["doc_id"] for r in result["rows"]] == ["paper_1"]
    assert result["rows"][0]["citation_count"] == 100


def test_find_field_title_rejects_non_paper_by(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="x", by="chunk", rank="semantic",
            top_k=5, field="title",
        )
    assert exc.value.code == "bad_field_by_combo"


def test_find_field_title_rejects_empty_query(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    with pytest.raises(queries.QueryError) as exc:
        queries.find(
            corpus, query="", by="paper", rank="semantic",
            top_k=5, field="title",
        )
    assert exc.value.code == "missing_query"


def test_read_doc_text_groups_consecutive_chunks_by_section(
    tmp_path: Path,
) -> None:
    corpus = _make_corpus(tmp_path / "c")
    out = queries.read_doc_text(corpus, "paper_0")
    segments = out["segments"]
    assert [s["section_path"] for s in segments] == [["intro"], ["body"]]
    assert all("text" in s and "chunk_ids" in s for s in segments)
    assert out["available_section_paths"] == [["intro"], ["body"]]


def test_read_doc_text_section_filter(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    out = queries.read_doc_text(corpus, "paper_0", sections=["intro"])
    assert [s["section_path"] for s in out["segments"]] == [["intro"]]
    assert out["matched_section_paths"] == [["intro"]]


def test_read_doc_text_section_filter_tolerates_numbering(
    tmp_path: Path,
) -> None:
    """Filter must hit ``"V. SUMMARY"``-style headings from a token like 'summary'."""
    import json as _json

    from wikify.api import Corpus
    root = tmp_path / "c"
    corpus = Corpus(root=root)
    corpus.ensure()
    (corpus.docs_dir / "p.json").write_text(_json.dumps({
        "id": "p", "source_path": "src/p.md", "kind": "md", "title": "P",
        "metadata": {}, "markdown_path": "markdown/p.md",
        "image_dir": "images/p/", "sections": [], "images": [],
        "abstract": "", "tldr": "", "n_chunks": 2, "n_tokens": 50,
        "citations": [], "equations": [], "figure_refs": [],
        "similar_to": [], "cites": [], "cites_same": [],
    }), encoding="utf-8")
    chunks = [
        {"id": "p__c0", "doc_id": "p", "ord": 0, "text": "intro text",
         "char_span": [0, 10], "section_path": ["I. INTRODUCTION"],
         "section_type": "body", "equation_ids": [], "is_boilerplate": False},
        {"id": "p__c1", "doc_id": "p", "ord": 1, "text": "summary text",
         "char_span": [10, 22], "section_path": ["V. SUMMARY"],
         "section_type": "body", "equation_ids": [], "is_boilerplate": False},
    ]
    (corpus.chunks_dir / "p.jsonl").write_text(
        "\n".join(_json.dumps(c) for c in chunks), encoding="utf-8",
    )
    (corpus.markdown_dir / "p.md").write_text("# P", encoding="utf-8")
    (corpus.manifest_path).write_text("{}", encoding="utf-8")

    out = queries.read_doc_text(corpus, "p", sections=["summary"])
    assert [s["section_path"] for s in out["segments"]] == [["V. SUMMARY"]]
    out_intro = queries.read_doc_text(corpus, "p", sections=["introduction"])
    assert [s["section_path"] for s in out_intro["segments"]] == [["I. INTRODUCTION"]]


def test_read_doc_text_skips_image_caption_chunks(tmp_path: Path) -> None:
    """Figure-caption chunks (``__image__`` section) must not appear in body text."""
    import json as _json

    from wikify.api import Corpus
    root = tmp_path / "c"
    corpus = Corpus(root=root)
    corpus.ensure()
    (corpus.docs_dir / "p.json").write_text(_json.dumps({
        "id": "p", "source_path": "src/p.md", "kind": "md", "title": "P",
        "metadata": {}, "markdown_path": "markdown/p.md",
        "image_dir": "images/p/", "sections": [], "images": [],
        "abstract": "", "tldr": "", "n_chunks": 2, "n_tokens": 50,
        "citations": [], "equations": [], "figure_refs": [],
        "similar_to": [], "cites": [], "cites_same": [],
    }), encoding="utf-8")
    chunks = [
        {"id": "p__c0", "doc_id": "p", "ord": 0, "text": "real body",
         "char_span": [0, 9], "section_path": ["Body"],
         "section_type": "body", "equation_ids": [], "is_boilerplate": False},
        {"id": "p__c1", "doc_id": "p", "ord": 1, "text": "Figure 1: caption stub",
         "char_span": [9, 30], "section_path": ["__image__"],
         "section_type": "body", "equation_ids": [], "is_boilerplate": False},
    ]
    (corpus.chunks_dir / "p.jsonl").write_text(
        "\n".join(_json.dumps(c) for c in chunks), encoding="utf-8",
    )
    (corpus.markdown_dir / "p.md").write_text("# P", encoding="utf-8")
    (corpus.manifest_path).write_text("{}", encoding="utf-8")

    out = queries.read_doc_text(corpus, "p")
    assert [s["section_path"] for s in out["segments"]] == [["Body"]]
    # The caption chunk is not in available_section_paths either.
    assert ["__image__"] not in out["available_section_paths"]


def test_doc_section_index(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    idx = queries.doc_section_index(corpus, "paper_0")
    paths = [s["section_path"] for s in idx]
    assert ["intro"] in paths
    assert ["body"] in paths
    assert all(s["n_chunks"] >= 1 for s in idx)
