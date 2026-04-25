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
    assert summary["has_knowledge_graph"] is False
