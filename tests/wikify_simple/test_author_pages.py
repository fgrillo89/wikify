"""Tests for distill/author_pages.py."""

from __future__ import annotations

from pathlib import Path

from wikify_simple.distill.author_pages import build_author_pages, merge_extracted_evidence
from wikify_simple.ingest.metadata import _is_valid_author
from wikify_simple.models import Document, Evidence
from wikify_simple.paths import BundlePaths
from wikify_simple.store.wiki_files import write_page


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
    assert "Alice Adams" == alice.id


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


def test_lead_paragraph_contains_name():
    docs = [_doc("d1", "Memristor theory and devices", ["Leon Chua"], 1971)]
    pages = build_author_pages(docs)
    chua = next(p for p in pages if p.title == "Leon Chua")
    lead = chua.body_markdown.splitlines()[0]
    assert "Leon Chua" in lead
    assert "1971" in lead
    # field hint drawn from title words
    assert "memristor" in chua.body_markdown.lower()


def test_wikilinks_replace_doc_refs():
    docs = [
        _doc("d1", "Paper One", ["Alice Adams"], 2020),
        _doc("d2", "Paper Two", ["Alice Adams"], 2021),
    ]
    pages = build_author_pages(docs)
    alice = next(p for p in pages if p.title == "Alice Adams")
    assert "[[Paper One]]" in alice.body_markdown
    assert "[[Paper Two]]" in alice.body_markdown
    assert "[doc:" not in alice.body_markdown
    assert "## Publications in this corpus" in alice.body_markdown


def test_frontmatter_has_author_tag(tmp_path: Path):
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    pages = build_author_pages(docs)
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    path = write_page(bundle, pages[0])
    text = path.read_text(encoding="utf-8")
    assert "tags: [author]" in text


def test_collaborators_section():
    docs = [
        _doc("d1", "Shared Paper", ["Alice Adams", "Bob Brown"], 2020),
        _doc("d2", "Alice Solo", ["Alice Adams"], 2021),
    ]
    pages = build_author_pages(docs)
    alice = next(p for p in pages if p.title == "Alice Adams")
    assert "## Collaborators" in alice.body_markdown
    assert "[[Bob Brown]]" in alice.body_markdown
    # Carol has no collaborators => no section
    carol_docs = [_doc("d9", "Solo", ["Carol Cole"], 2020)]
    pages2 = build_author_pages(carol_docs)
    carol = next(p for p in pages2 if p.title == "Carol Cole")
    assert "## Collaborators" not in carol.body_markdown
    assert alice.provenance["collaborator_count"] == 1


def test_incremental_merge_existing_links(tmp_path: Path):
    # Simulate a prior run that wrote an Alice page with Legacy Paper.
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    prior = bundle.people_dir / "Alice Adams.md"
    prior.write_text(
        "---\nid: Alice Adams\nkind: person\n---\n\n"
        "**Alice Adams** appears.\n\n"
        "## Publications in this corpus\n\n"
        "- 2019. [[Legacy Paper]]\n",
        encoding="utf-8",
    )
    docs = [
        _doc("d1", "Paper One", ["Alice Adams"], 2020),
        _doc("d2", "Paper Two", ["Alice Adams"], 2021),
    ]
    pages = build_author_pages(docs, existing_page_dir=bundle.people_dir)
    alice = next(p for p in pages if p.title == "Alice Adams")
    body = alice.body_markdown
    assert "[[Paper One]]" in body
    assert "[[Paper Two]]" in body
    assert "[[Legacy Paper]]" in body


def test_merge_extracted_evidence_appends():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    pages = build_author_pages(docs)
    alice = next(p for p in pages if p.title == "Alice Adams")
    original_count = len(alice.evidence)
    original_body = alice.body_markdown

    new_evidence = [
        Evidence(marker="ex1", chunk_id="c99", doc_id="d99", quote="new quote"),
        Evidence(marker="ex2", chunk_id="c100", doc_id="d100", quote="another quote"),
    ]
    merge_extracted_evidence(alice, new_evidence)
    assert len(alice.evidence) == original_count + 2
    # Skeleton preserved.
    assert alice.body_markdown == original_body


def test_merge_extracted_evidence_deduplicates():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    pages = build_author_pages(docs)
    alice = next(p for p in pages if p.title == "Alice Adams")
    existing_chunk = alice.evidence[0].chunk_id

    # Try to merge evidence with an already-existing chunk_id.
    new_evidence = [
        Evidence(marker="ex1", chunk_id=existing_chunk, doc_id="d1", quote="dup"),
        Evidence(marker="ex2", chunk_id="c_new", doc_id="d_new", quote="fresh"),
    ]
    before = len(alice.evidence)
    merge_extracted_evidence(alice, new_evidence)
    # Only the non-duplicate should be added.
    assert len(alice.evidence) == before + 1
