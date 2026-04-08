"""Tests for ingest/bibtex.py."""

from __future__ import annotations

import bibtexparser

from wikify_simple.ingest.bibtex import paper_to_bibtex, write_corpus_bibtex
from wikify_simple.models import Document
from wikify_simple.paths import CorpusPaths


def _doc(doc_id: str, title: str, authors: list[str], year: int) -> Document:
    return Document(
        id=doc_id,
        source_path=f"/tmp/{doc_id}.pdf",
        kind="pdf",
        title=title,
        metadata={"authors": authors, "year": year, "doi": "10.1000/test"},
        markdown_path="",
        image_dir="",
    )


def test_paper_to_bibtex_roundtrip():
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov", "G. Snider"], 2008)
    bib = paper_to_bibtex(doc)
    db = bibtexparser.loads(bib)
    assert len(db.entries) == 1
    e = db.entries[0]
    assert e["ID"] == "paper_abc123"
    assert "Strukov" in e["author"]
    assert e["year"] == "2008"
    assert e["doi"] == "10.1000/test"


def test_write_corpus_bibtex(tmp_path):
    docs = [
        _doc("a_1", "Title A", ["Alice"], 2020),
        _doc("b_2", "Title B", ["Bob"], 2021),
        _doc("c_3", "Title C", ["Carol"], 2022),
    ]
    corpus = CorpusPaths(root=tmp_path / "corpus")
    bib_path = write_corpus_bibtex(corpus, docs)
    assert bib_path.exists()
    db = bibtexparser.loads(bib_path.read_text(encoding="utf-8"))
    assert len(db.entries) == 3
