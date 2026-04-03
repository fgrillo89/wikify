"""Tests for wikify.extract.chunker."""

from __future__ import annotations

from unittest.mock import patch

from wikify.extract.chunker import _split_into_sections, chunk_sections

PAPER_ID = "test-paper-001"


# ── _split_into_sections ──────────────────────────────────────────────────────


def test_split_single_section_no_headings():
    md = "This is a paragraph.\n\nAnother paragraph here."
    sections = _split_into_sections(md)
    assert len(sections) == 1
    section_path, text = sections[0]
    assert section_path == "root"
    assert "This is a paragraph" in text


def test_split_respects_heading_boundaries():
    md = "# Introduction\n\nIntro text here.\n\n# Methods\n\nMethods text here."
    sections = _split_into_sections(md)
    paths = [s[0] for s in sections]
    texts = [s[1] for s in sections]
    assert "Introduction" in paths
    assert "Methods" in paths
    assert any("Intro text" in t for t in texts)
    assert any("Methods text" in t for t in texts)


def test_split_nested_headings():
    md = "# Results\n\n## Subsection A\n\nText A.\n\n## Subsection B\n\nText B."
    sections = _split_into_sections(md)
    paths = [s[0] for s in sections]
    assert any("Results.Subsection A" in p for p in paths)
    assert any("Results.Subsection B" in p for p in paths)


def test_split_empty_sections_skipped():
    md = "# Heading\n\n\n\n# Another\n\nContent here."
    sections = _split_into_sections(md)
    # Only "Another" has content; empty sections should be skipped
    assert all(text.strip() for _, text in sections)


def test_split_heading_stack_resets_on_same_level():
    md = "# A\n\nText A.\n\n# B\n\nText B."
    sections = _split_into_sections(md)
    paths = [s[0] for s in sections]
    assert "A" in paths
    assert "B" in paths
    # B should not be nested under A
    assert not any("A.B" in p for p in paths)


# ── chunk_sections ────────────────────────────────────────────────────────────


def _make_long_paragraph(n_words: int = 200) -> str:
    """Create a paragraph with n_words words."""
    return " ".join(f"word{i}" for i in range(n_words)) + "."


def test_chunk_sections_returns_chunks():
    md = "# Introduction\n\n" + _make_long_paragraph(100)
    chunks = chunk_sections(md, {}, PAPER_ID)
    assert len(chunks) > 0


def test_chunk_sections_paper_id_set():
    md = "# Introduction\n\n" + _make_long_paragraph(50)
    chunks = chunk_sections(md, {}, PAPER_ID)
    for chunk in chunks:
        assert chunk.paper_id == PAPER_ID


def test_chunk_sections_section_path_set():
    md = "# Introduction\n\nIntro text is here.\n\n# Methods\n\nMethods described here."
    chunks = chunk_sections(md, {}, PAPER_ID)
    paths = {c.section_path for c in chunks}
    assert "Introduction" in paths
    assert "Methods" in paths


def test_chunk_sections_no_cross_section_chunks():
    """Chunks should not span multiple sections."""
    intro_text = _make_long_paragraph(50)
    methods_text = _make_long_paragraph(50)
    md = f"# Introduction\n\n{intro_text}\n\n# Methods\n\n{methods_text}"
    chunks = chunk_sections(md, {}, PAPER_ID)

    # Each chunk should be wholly within one section
    for chunk in chunks:
        if "Introduction" in chunk.section_path:
            assert "word0" in chunk.content  # from intro paragraph
        if "Methods" in chunk.section_path:
            # Methods section chunks should contain methods-only content
            assert chunk.section_path == "Methods"


def test_chunk_sections_chunk_index_ordered():
    """chunk_index should start at 0 and increment within a section."""
    # Create a very long section to force multiple chunks
    long_text = "\n\n".join(_make_long_paragraph(200) for _ in range(5))
    md = f"# BigSection\n\n{long_text}"

    with patch("wikify.extract.chunker.settings") as mock_settings:
        mock_settings.chunk_target_tokens = 200
        mock_settings.chunk_max_tokens = 250
        mock_settings.chunk_overlap_tokens = 20
        chunks = chunk_sections(md, {}, PAPER_ID)

    section_chunks = [c for c in chunks if c.section_path == "BigSection"]
    if len(section_chunks) > 1:
        indices = [c.chunk_index for c in section_chunks]
        assert indices == list(range(len(indices)))


def test_chunk_sections_has_citations_flag():
    md = "# Body\n\nThis references earlier work [Smith et al. 2020] and more [42]."
    chunks = chunk_sections(md, {}, PAPER_ID)
    assert any(c.has_citations for c in chunks)


def test_chunk_sections_no_citations_flag():
    md = "# Body\n\nThis paragraph has no citations at all."
    chunks = chunk_sections(md, {}, PAPER_ID)
    assert all(not c.has_citations for c in chunks)


def test_chunk_sections_has_equations_flag():
    md = "# Methods\n\nThe energy is given by $$E = mc^2$$."
    chunks = chunk_sections(md, {}, PAPER_ID)
    assert any(c.has_equations for c in chunks)


def test_chunk_sections_no_equations_flag():
    md = "# Body\n\nPlain text with no equations."
    chunks = chunk_sections(md, {}, PAPER_ID)
    assert all(not c.has_equations for c in chunks)


def test_chunk_sections_token_count_positive():
    md = "# Intro\n\nSome text here that is definitely not empty."
    chunks = chunk_sections(md, {}, PAPER_ID)
    for chunk in chunks:
        assert chunk.token_count > 0


def test_chunk_sections_ids_unique():
    md = "# A\n\n" + _make_long_paragraph(50) + "\n\n# B\n\n" + _make_long_paragraph(50)
    chunks = chunk_sections(md, {}, PAPER_ID)
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunk_sections_empty_markdown():
    chunks = chunk_sections("", {}, PAPER_ID)
    assert chunks == []
