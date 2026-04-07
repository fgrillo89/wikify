"""Tests for wikify.ingest.vault.templates."""

from __future__ import annotations

import yaml

from wikify.ingest.vault.templates import (
    _strip_citation_brackets,
    author_note,
    paper_note,
    topic_note,
)

# ── _strip_citation_brackets ─────────────────────────────────────────────────


def test_strip_double_bracket_numbers_removed():
    result = _strip_citation_brackets("See [[4]] for details.")
    assert "[[4]]" not in result
    assert "4" not in result or "(4)" not in result  # whole wikilink removed


def test_strip_double_bracket_range_removed():
    result = _strip_citation_brackets("References [[10-12]] here.")
    assert "[[10-12]]" not in result


def test_strip_double_bracket_comma_list_removed():
    result = _strip_citation_brackets("Papers [[4,5,6]] confirm this.")
    assert "[[4,5,6]]" not in result


def test_strip_single_bracket_converted_to_parens():
    result = _strip_citation_brackets("See [4] for more.")
    assert "[4]" not in result
    assert "(4)" in result


def test_strip_single_bracket_comma_list():
    result = _strip_citation_brackets("See [4,5] and [10,11].")
    assert "[4,5]" not in result
    assert "(4,5)" in result
    assert "(10,11)" in result


def test_strip_preserves_non_numeric_brackets():
    # Wikilinks with text should NOT be stripped
    result = _strip_citation_brackets("See [[Smith 2020]] for details.")
    assert "[[Smith 2020]]" in result


def test_strip_no_citations():
    text = "No citations here at all."
    result = _strip_citation_brackets(text)
    assert result == text


# ── paper_note ───────────────────────────────────────────────────────────────


def _extract_frontmatter(note: str) -> dict:
    """Parse YAML frontmatter from a note string."""
    assert note.startswith("---\n"), "Note must start with frontmatter"
    end = note.index("\n---\n", 4)
    fm_text = note[4:end]
    return yaml.safe_load(fm_text)


def test_paper_note_frontmatter_is_valid_yaml():
    note = paper_note(
        title="Test Paper",
        authors=["Alice Brown", "Bob Green"],
        year=2022,
        doi="10.1234/test",
        summary="Short abstract.",
        file_hash="abc123",
        source_path="data/test.pdf",
    )
    fm = _extract_frontmatter(note)
    assert fm["title"] == "Test Paper"
    assert fm["year"] == 2022
    assert fm["doi"] == "10.1234/test"


def test_paper_note_authors_as_plain_text():
    """Authors stored as plain text (no wikilinks) to keep the graph clean."""
    note = paper_note(
        title="T",
        authors=["Alice Brown", "Bob Green"],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
    )
    fm = _extract_frontmatter(note)
    assert "Alice Brown" in fm["authors"]
    assert "Bob Green" in fm["authors"]
    assert not any("[[" in a for a in fm["authors"])


def test_paper_note_tags_include_source_paper():
    note = paper_note(
        title="T",
        authors=[],
        year=None,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
    )
    fm = _extract_frontmatter(note)
    assert "source/paper" in fm["tags"]


def test_paper_note_doi_absent_when_none():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
    )
    fm = _extract_frontmatter(note)
    assert "doi" not in fm


def test_paper_note_summary_citation_brackets_stripped():
    summary_text = "This extends [4] and [10,11] prior work."
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=summary_text,
        file_hash="x",
        source_path="data/t.pdf",
    )
    assert "[4]" not in note
    assert "(4)" in note


def test_paper_note_full_text_callout_included():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        full_text="The full body of the paper goes here.",
    )
    assert "> [!quote]- Full Text" in note
    assert "The full body of the paper goes here." in note


def test_paper_note_full_text_callout_absent_when_none():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        full_text=None,
    )
    assert "[!quote]" not in note


def test_paper_note_full_text_lines_prefixed():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        full_text="Line one.\nLine two.",
    )
    assert "> Line one." in note
    assert "> Line two." in note


def test_paper_note_source_path_creates_file_link():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/test.pdf",
    )
    assert "file:///" in note
    assert "Open original file" in note


def test_paper_note_topics_in_frontmatter():
    """Topics stored as plain text (no wikilinks) to keep the graph clean."""
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        topics=["ALD", "Memristors"],
    )
    fm = _extract_frontmatter(note)
    assert "ALD" in fm["topics"]
    assert "Memristors" in fm["topics"]
    assert not any("[[" in t for t in fm["topics"])


def test_paper_note_cites_in_frontmatter_and_section():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        cites=["Paper A", "Paper B"],
    )
    fm = _extract_frontmatter(note)
    assert "[[papers/Paper A]]" in fm["cites"]
    # Also appears in a Cites section
    assert "## Cites" in note


def test_paper_note_statistics_section_always_present():
    note = paper_note(
        title="T",
        authors=[],
        year=2020,
        doi=None,
        summary=None,
        file_hash="x",
        source_path="data/t.pdf",
        chunks_count=5,
        figures_count=2,
    )
    assert "## Statistics" in note
    assert "**Chunks**: 5" in note
    assert "**Figures**: 2" in note


# ── author_note ───────────────────────────────────────────────────────────────


def test_author_note_frontmatter_valid():
    note = author_note("Alice Brown", ["Paper One", "Paper Two"])
    fm = _extract_frontmatter(note)
    assert fm["name"] == "Alice Brown"
    assert "author" in fm["tags"]


def test_author_note_paper_links():
    note = author_note("Alice Brown", ["Paper One", "Paper Two"])
    assert "[[papers/Paper One]]" in note
    assert "[[papers/Paper Two]]" in note


def test_author_note_papers_section():
    note = author_note("Alice Brown", ["Paper One"])
    assert "## Papers" in note


def test_author_note_empty_papers():
    note = author_note("Lone Wolf", [])
    assert "## Papers" in note
    fm = _extract_frontmatter(note)
    assert fm["name"] == "Lone Wolf"


# ── topic_note ────────────────────────────────────────────────────────────────


def test_topic_note_frontmatter_valid():
    note = topic_note("Atomic Layer Deposition", ["Paper A", "Paper B"])
    fm = _extract_frontmatter(note)
    assert fm["name"] == "Atomic Layer Deposition"
    assert "topic" in fm["tags"]


def test_topic_note_paper_links():
    note = topic_note("ALD", ["Paper A", "Paper B"])
    assert "[[papers/Paper A]]" in note
    assert "[[papers/Paper B]]" in note


def test_topic_note_related_papers_section():
    note = topic_note("ALD", ["Paper A"])
    assert "## Related Papers" in note


def test_topic_note_empty_papers():
    note = topic_note("New Topic", [])
    fm = _extract_frontmatter(note)
    assert fm["name"] == "New Topic"
