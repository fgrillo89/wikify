"""Tests for distill/author_pages.py."""

from __future__ import annotations

from wikify_simple.distill.author_pages import build_author_pages
from wikify_simple.ingest.metadata import _is_valid_author
from wikify_simple.models import Document


def _doc(doc_id: str, title: str, authors: list[str], year: int, citations=None) -> Document:
    return Document(
        id=doc_id,
        source_path=f"/tmp/{doc_id}.pdf",
        kind="pdf",
        title=title,
        metadata={"authors": authors, "year": year},
        markdown_path="",
        image_dir="",
        citations=citations or [],
    )


def test_unique_author_per_doc():
    docs = [
        _doc("d1", "Paper One", ["Alice Adams", "Bob Brown"], 2020),
        _doc("d2", "Paper Two", ["Alice Adams"], 2021),
        _doc("d3", "Paper Three", ["Carol Cole"], 2022),
    ]
    pages = build_author_pages(docs)
    titles = {p.title for p in pages}
    assert "Alice Adams" in titles
    assert "Bob Brown" in titles
    assert "Carol Cole" in titles
    alice = next(p for p in pages if p.title == "Alice Adams")
    assert alice.provenance["primary_count"] == 2
    assert "person-alice-adams" == alice.id


def test_citation_mined_authors_get_pages():
    docs = [
        _doc(
            "d1",
            "Paper One",
            ["Alice Adams"],
            2020,
            citations=[
                {
                    "ord": 0,
                    "raw_text": "...",
                    "authors": ["David Drake"],
                    "year": 2010,
                    "title": "Old Work",
                    "venue": "",
                    "doi": None,
                }
            ],
        ),
    ]
    pages = build_author_pages(docs)
    drake = next((p for p in pages if p.title == "David Drake"), None)
    assert drake is not None
    assert drake.provenance["from_citation_count"] >= 1
    assert drake.provenance["primary_count"] == 0


def test_validator_rejects_garbage():
    assert not _is_valid_author("Department")
    assert not _is_valid_author("A")
    assert not _is_valid_author("Et Al")
    assert _is_valid_author("Alice Adams")


def test_pages_have_evidence():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    pages = build_author_pages(docs)
    assert all(len(p.evidence) >= 1 for p in pages)
