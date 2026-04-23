"""Tests for the canonical-abstract tagger.

Verifies the 5-tier selection algorithm + the per-doc invariants:
  - exactly one chunk per body-bearing doc gets ``section_type='abstract'``
  - prior tags are cleared on re-run (idempotent)
  - boilerplate-flagged chunks are skipped at every walk step
"""

from __future__ import annotations

from wikify.ingest.abstract_tagger import (
    ABSTRACT_MIN_WORDS,
    find_canonical_abstract,
    tag_abstracts,
)
from wikify.models import Chunk


def _chunk(
    cid: str,
    doc_id: str,
    ord_: int,
    *,
    section_type: str = "body",
    section_path: list[str] | None = None,
    n_words: int = 50,
    is_boilerplate: bool = False,
) -> Chunk:
    text = " ".join(["word"] * n_words)
    return Chunk(
        id=cid,
        doc_id=doc_id,
        ord=ord_,
        text=text,
        char_span=(0, len(text)),
        section_path=section_path or [],
        section_type=section_type,
        is_boilerplate=is_boilerplate,
    )


# --- algorithm tiers -----------------------------------------------------


def test_tier1_classifier_tagged_abstract_in_range_wins():
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=50),
        _chunk("d1#c1", "d1", 1, section_type="abstract", n_words=150),  # winner
        _chunk("d1#c2", "d1", 2, n_words=900),
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


def test_tier2_fuzzy_summary_heading_wins_when_classifier_missed():
    """A SUMMARY heading the classifier didn't tag as abstract still wins
    via the fuzzy heading match in the first 1/3 window."""
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=20),  # short header
        _chunk(
            "d1#c1", "d1", 1,
            section_path=["Paper Title", "EXECUTIVE SUMMARY"],
            n_words=200,
        ),  # winner
        _chunk("d1#c2", "d1", 2, n_words=900),
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


def test_tier3_first_substantive_in_window():
    """No abstract section + no fuzzy match → first chunk in window
    that clears the word floor."""
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=20),
        _chunk("d1#c1", "d1", 1, n_words=150),  # winner
        _chunk("d1#c2", "d1", 2, n_words=80),
        _chunk("d1#c9", "d1", 9, n_words=500),  # bigger but later
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


def test_tier5_longest_in_doc_when_nothing_clears_floor():
    """All chunks under the 100-word floor → longest chunk wins."""
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=10),
        _chunk("d1#c1", "d1", 1, n_words=40),
        _chunk("d1#c2", "d1", 2, n_words=80),  # longest
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c2"


# --- skip rules ----------------------------------------------------------


def test_boilerplate_chunks_are_skipped_in_walk():
    """A 600-word boilerplate chunk in front-matter must be skipped; the
    picker walks past it to the next substantive chunk."""
    chunks = [
        _chunk(
            "d1#c0", "d1", 0,
            n_words=300, is_boilerplate=True,  # would otherwise win tier 3
        ),
        _chunk("d1#c1", "d1", 1, n_words=200),  # winner
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


def test_skip_section_types_excluded():
    """References / acknowledgments / appendix never qualify."""
    chunks = [
        _chunk("d1#c0", "d1", 0, section_type="references", n_words=900),
        _chunk("d1#c1", "d1", 1, section_type="acknowledgments", n_words=400),
        _chunk("d1#c2", "d1", 2, n_words=200),  # winner
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c2"


def test_caption_chunks_excluded():
    """Image-caption chunks (__image__ section_path[0]) are excluded."""
    chunks = [
        _chunk("d1#c0", "d1", 0, section_path=["__image__"], n_words=400),
        _chunk("d1#c1", "d1", 1, n_words=200),  # winner
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


def test_under_range_abstract_classifier_chunk_falls_through():
    """A 50-word stub tagged ``abstract`` doesn't qualify in tier 1; the
    picker falls through to the next substantive chunk."""
    chunks = [
        _chunk("d1#c0", "d1", 0, section_type="abstract", n_words=50),  # stub
        _chunk("d1#c1", "d1", 1, n_words=ABSTRACT_MIN_WORDS),  # tier-3 winner
    ]
    pick = find_canonical_abstract(chunks)
    assert pick is not None and pick.id == "d1#c1"


# --- tag_abstracts (mutation invariants) --------------------------------


def test_tag_abstracts_marks_exactly_one_per_doc():
    """Each doc with body content gets exactly one chunk tagged."""
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=30),
        _chunk("d1#c1", "d1", 1, n_words=200),
        _chunk("d2#c0", "d2", 0, section_type="abstract", n_words=150),
        _chunk("d2#c1", "d2", 1, n_words=400),
    ]
    tag_abstracts(chunks)
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    for did, cs in by_doc.items():
        n_abstract = sum(1 for c in cs if c.section_type == "abstract")
        assert n_abstract == 1, f"{did}: expected exactly one abstract chunk, got {n_abstract}"


def test_tag_abstracts_clears_stale_tags():
    """If a chunk was previously tagged abstract but is no longer the
    winner under the current rule, the stale tag is removed."""
    chunks = [
        # Old pick: 50-word "abstract" stub that's now under the floor.
        _chunk("d1#c0", "d1", 0, section_type="abstract", n_words=50),
        # New pick: substantial body chunk.
        _chunk("d1#c1", "d1", 1, n_words=300),
    ]
    tag_abstracts(chunks)
    assert chunks[0].section_type == "body"  # stale tag cleared
    assert chunks[1].section_type == "abstract"


def test_tag_abstracts_is_idempotent():
    """Running twice produces the same result."""
    chunks = [
        _chunk("d1#c0", "d1", 0, n_words=30),
        _chunk("d1#c1", "d1", 1, n_words=200),
    ]
    tag_abstracts(chunks)
    first = [c.section_type for c in chunks]
    tag_abstracts(chunks)
    assert [c.section_type for c in chunks] == first


def test_tag_abstracts_handles_doc_with_only_skip_chunks():
    """A doc whose every chunk is references/acks gets no abstract tag
    (no usable chunk to tag). Doesn't crash."""
    chunks = [
        _chunk("d1#c0", "d1", 0, section_type="references", n_words=900),
        _chunk("d1#c1", "d1", 1, section_type="acknowledgments", n_words=400),
    ]
    tag_abstracts(chunks)
    assert all(c.section_type != "abstract" for c in chunks)
