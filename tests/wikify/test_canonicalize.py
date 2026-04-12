"""Tests for distill/canonicalize.py -- person candidate handling."""

from wikify.schema import ExtractedConcept
from wikify.distill.dossier import Candidate, canonicalize
from wikify.models import Evidence, WikiPage


def _concept_candidate(title: str, chunk_id: str, doc_id: str) -> Candidate:
    return Candidate(
        concept=ExtractedConcept(
            title=title,
            aliases=[],
            kind="article",
            quote="sample quote for testing purposes here",
            category="method",
        ),
        chunk_id=chunk_id,
        doc_id=doc_id,
    )


def _person_candidate(name: str, chunk_id: str, doc_id: str) -> Candidate:
    return Candidate(
        concept=ExtractedConcept(
            title=name,
            aliases=[],
            kind="person",
            quote="this person contributed significant work here",
        ),
        chunk_id=chunk_id,
        doc_id=doc_id,
    )


def _author_page(name: str) -> WikiPage:
    return WikiPage(
        id=name,
        kind="person",
        title=name,
        aliases=[],
        body_markdown=f"**{name}** is associated with testing.",
        evidence=[Evidence(marker="e1", chunk_id="c0", doc_id="d0", quote=name)],
        provenance={"source": "deterministic"},
    )


def test_concept_candidate_creates_new_page():
    """Concept candidates with no match create a new WikiPage."""
    pages = canonicalize(
        [_concept_candidate("Atomic Layer Deposition", "c1", "d1")],
        existing=[],
    )
    assert len(pages) == 1
    assert pages[0].kind == "article"
    assert pages[0].title == "Atomic Layer Deposition"


def test_person_candidate_merges_into_existing_author():
    """Person candidate matching an existing author page merges evidence."""
    author = _author_page("Alice Adams")
    assert len(author.evidence) == 1
    pages = canonicalize(
        [_person_candidate("Alice Adams", "c1", "d1")],
        existing=[author],
    )
    alice = next(p for p in pages if p.title == "Alice Adams")
    assert len(alice.evidence) == 2
    # Body (skeleton) preserved.
    assert "**Alice Adams**" in alice.body_markdown


def test_person_candidate_no_match_creates_new_page():
    """Person candidate with no existing page creates a new extracted person."""
    pages = canonicalize(
        [_person_candidate("Richard Feynman", "c1", "d1")],
        existing=[],
    )
    assert len(pages) == 1
    p = pages[0]
    assert p.kind == "person"
    assert p.title == "Richard Feynman"
    assert p.provenance.get("source") == "extraction"
    assert len(p.evidence) == 1


def test_concept_candidate_unchanged_by_person_logic():
    """Concept candidates are unaffected by person handling."""
    author = _author_page("Alice Adams")
    pages = canonicalize(
        [
            _concept_candidate("Photocatalysis", "c1", "d1"),
            _person_candidate("Alice Adams", "c2", "d2"),
        ],
        existing=[author],
    )
    concepts = [p for p in pages if p.kind == "article"]
    people = [p for p in pages if p.kind == "person"]
    assert len(concepts) == 1
    assert concepts[0].title == "Photocatalysis"
    assert len(people) == 1
    assert people[0].title == "Alice Adams"
    assert len(people[0].evidence) == 2  # original + merged


def test_multiple_person_candidates_same_name_merge():
    """Multiple person candidates for the same name merge into one page."""
    pages = canonicalize(
        [
            _person_candidate("Richard Feynman", "c1", "d1"),
            _person_candidate("Richard Feynman", "c2", "d2"),
        ],
        existing=[],
    )
    feynman_pages = [p for p in pages if "feynman" in p.title.lower()]
    assert len(feynman_pages) == 1
    assert len(feynman_pages[0].evidence) == 2
