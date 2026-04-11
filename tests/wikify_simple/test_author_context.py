"""Tests for distill/write/author_context.py."""

from wikify_simple.distill.write.author_context import (
    AuthorContext,
    _author_key,
    build_author_context,
)
from wikify_simple.models import Document


def _doc(
    doc_id: str,
    title: str,
    authors: list[str],
    year: int | None,
    citations: list[dict] | None = None,
) -> Document:
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


# --- publication buckets ---------------------------------------------------


def test_primary_publication_collected():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    ctx = build_author_context(docs)
    key = _author_key("Alice Adams")
    assert key in ctx
    alice = ctx[key]
    assert len(alice.primary_publications) == 1
    assert alice.primary_publications[0].doc_id == "d1"
    assert alice.primary_publications[0].year == 2020


def test_multiple_papers_same_author():
    docs = [
        _doc("d1", "Paper One", ["Alice Adams"], 2020),
        _doc("d2", "Paper Two", ["Alice Adams"], 2022),
    ]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert len(alice.primary_publications) == 2
    assert alice.year_range == (2020, 2022)


def test_year_range_single_paper():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2021)]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert alice.year_range == (2021, 2021)


def test_year_range_none_when_no_years():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], None)]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert alice.year_range is None


def test_cited_works_collected():
    docs = [
        _doc(
            "d1",
            "Paper One",
            ["Alice Adams"],
            2020,
            citations=[
                {
                    "authors": ["Bob Brown"],
                    "year": 2010,
                    "title": "Early Memristor Work",
                }
            ],
        )
    ]
    ctx = build_author_context(docs)
    key = _author_key("Bob Brown")
    assert key in ctx
    bob = ctx[key]
    assert len(bob.cited_works) == 1
    assert bob.cited_works[0].title == "Early Memristor Work"
    assert bob.cited_works[0].year == 2010
    assert bob.cited_works[0].citing_doc_id == "d1"


def test_primary_and_cited_separate():
    """An author who is both primary and cited gets both buckets."""
    docs = [
        _doc("d1", "Paper One", ["Alice Adams"], 2020),
        _doc(
            "d2",
            "Paper Two",
            ["Carol Cole"],
            2021,
            citations=[
                {"authors": ["Alice Adams"], "year": 2018, "title": "Prior Alice Work"}
            ],
        ),
    ]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert len(alice.primary_publications) == 1
    assert len(alice.cited_works) == 1


# --- coauthor detection ---------------------------------------------------


def test_collaborators_detected():
    docs = [_doc("d1", "Shared Paper", ["Alice Adams", "Bob Brown"], 2020)]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    bob = ctx[_author_key("Bob Brown")]
    assert "Bob Brown" in alice.collaborators
    assert "Alice Adams" in bob.collaborators


def test_solo_author_no_collaborators():
    docs = [_doc("d1", "Solo Paper", ["Alice Adams"], 2020)]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert alice.collaborators == []


def test_collaborators_across_docs():
    docs = [
        _doc("d1", "Paper One", ["Alice Adams", "Bob Brown"], 2020),
        _doc("d2", "Paper Two", ["Alice Adams", "Carol Cole"], 2021),
    ]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert "Bob Brown" in alice.collaborators
    assert "Carol Cole" in alice.collaborators


# --- robustness to missing/bad metadata -----------------------------------


def test_doc_with_no_author_field_does_not_crash():
    doc = Document(
        id="d1",
        source_path="/tmp/d1.pdf",
        kind="pdf",
        title="No Author Paper",
        metadata={},  # no 'authors' key
        markdown_path="",
        image_dir="",
    )
    ctx = build_author_context([doc])
    # No crash; result is empty or contains only citation authors
    assert isinstance(ctx, dict)


def test_doc_with_none_authors_does_not_crash():
    doc = Document(
        id="d1",
        source_path="/tmp/d1.pdf",
        kind="pdf",
        title="None Authors Paper",
        metadata={"authors": None, "year": 2020},
        markdown_path="",
        image_dir="",
    )
    ctx = build_author_context([doc])
    assert isinstance(ctx, dict)


def test_garbage_author_names_filtered():
    """Single-word names and 'et al' are not valid authors."""
    docs = [_doc("d1", "Paper One", ["Department", "A", "Et Al", "Alice Adams"], 2020)]
    ctx = build_author_context(docs)
    assert _author_key("Alice Adams") in ctx
    assert _author_key("Department") not in ctx
    assert _author_key("Et Al") not in ctx


def test_empty_docs_returns_empty():
    ctx = build_author_context([])
    assert ctx == {}


def test_returned_type_is_authcontext():
    docs = [_doc("d1", "Paper One", ["Alice Adams"], 2020)]
    ctx = build_author_context(docs)
    alice = ctx[_author_key("Alice Adams")]
    assert isinstance(alice, AuthorContext)
