"""Tests for the markdown ingester."""

from __future__ import annotations

from wikify.ingest.markdown import (
    _extract_authors,
    _extract_title,
    _extract_year,
    _parse_frontmatter,
    parse_markdown,
)

# ── Frontmatter parsing ───────────────────────────────────────────────────────


def test_parse_frontmatter_with_valid_yaml(tmp_path):
    text = "---\ntitle: My Note\nauthor: Alice\n---\n\nBody text here."
    meta, body = _parse_frontmatter(text)
    assert meta.get("title") == "My Note"
    assert "Body text here" in body


def test_parse_frontmatter_no_frontmatter():
    text = "# Just a heading\n\nSome content."
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    assert "Just a heading" in body


def test_parse_frontmatter_empty_body():
    text = "---\ntitle: Empty\n---\n"
    meta, body = _parse_frontmatter(text)
    assert meta.get("title") == "Empty"


# ── Title extraction ──────────────────────────────────────────────────────────


def test_extract_title_from_frontmatter():
    meta = {"title": "Frontmatter Title"}
    body = "# Heading Title\n\nSome content."
    assert _extract_title(meta, body, "stem") == "Frontmatter Title"


def test_extract_title_from_h1():
    meta = {}
    body = "# My Article\n\nContent here."
    assert _extract_title(meta, body, "stem") == "My Article"


def test_extract_title_fallback_stem():
    meta = {}
    body = "No heading content."
    assert _extract_title(meta, body, "my_file") == "my_file"


# ── Year extraction ───────────────────────────────────────────────────────────


def test_extract_year_from_date_field():
    meta = {"date": "2023-11-15"}
    assert _extract_year(meta) == 2023


def test_extract_year_from_created_field():
    meta = {"created": "2021-06-01T12:00:00"}
    assert _extract_year(meta) == 2021


def test_extract_year_missing():
    meta = {}
    assert _extract_year(meta) is None


def test_extract_year_invalid():
    meta = {"date": "not-a-date"}
    assert _extract_year(meta) is None


# ── Author extraction ─────────────────────────────────────────────────────────


def test_extract_authors_list():
    meta = {"authors": ["Alice Smith", "Bob Jones"]}
    assert _extract_authors(meta) == ["Alice Smith", "Bob Jones"]


def test_extract_authors_string_comma():
    meta = {"author": "Alice Smith, Bob Jones"}
    result = _extract_authors(meta)
    assert "Alice Smith" in result
    assert "Bob Jones" in result


def test_extract_authors_empty():
    meta = {}
    assert _extract_authors(meta) == []


# ── parse_markdown integration ────────────────────────────────────────────────


def test_parse_markdown_basic(tmp_path, monkeypatch):
    """parse_markdown returns a Paper with correct metadata and non-empty chunks."""
    # Monkeypatch the DB session to avoid real DB in tests
    monkeypatch.setattr(
        "wikify.ingest.markdown.chunk_sections",
        lambda text, tree, pid: [],  # return empty chunks for simplicity
    )

    md_file = tmp_path / "test_note.md"
    md_file.write_text(
        "---\ntitle: ALD Basics\nauthor: Dr. Smith\ndate: 2022-03-10\n---\n\n"
        "# Introduction\n\nALD is a thin-film deposition technique.\n\n"
        "## Methods\n\nThe process involves alternating precursors.",
        encoding="utf-8",
    )

    paper, chunks = parse_markdown(md_file)

    assert paper.title == "ALD Basics"
    assert paper.year == 2022
    assert "Dr. Smith" in paper.authors
    assert paper.doc_type == "markdown"
    assert paper.source_path == str(md_file)
    assert paper.file_hash != ""


def test_parse_markdown_no_frontmatter(tmp_path, monkeypatch):
    """parse_markdown handles files without frontmatter."""
    monkeypatch.setattr(
        "wikify.ingest.markdown.chunk_sections",
        lambda text, tree, pid: [],
    )

    md_file = tmp_path / "plain.md"
    md_file.write_text(
        "# My Document\n\nThis is plain content without frontmatter.",
        encoding="utf-8",
    )

    paper, chunks = parse_markdown(md_file)
    assert paper.title == "My Document"
    assert paper.doc_type == "markdown"


def test_parse_markdown_txt_file(tmp_path, monkeypatch):
    """parse_markdown works with .txt files."""
    monkeypatch.setattr(
        "wikify.ingest.markdown.chunk_sections",
        lambda text, tree, pid: [],
    )

    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Some plain text notes.\n\nMore content.", encoding="utf-8")

    paper, chunks = parse_markdown(txt_file)
    assert paper.doc_type == "markdown"
    assert paper.title == "notes"  # fallback to stem


def test_ingest_markdown_skips_existing(tmp_path, monkeypatch):
    """ingest_markdown returns 0 and skips if paper already in DB."""
    from wikify.ingest.markdown import ingest_markdown

    md_file = tmp_path / "existing.md"
    md_file.write_text("# Existing\n\nContent.", encoding="utf-8")

    # Monkeypatch session.get to return a truthy object
    class FakePaper:
        pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, model, key):
            return FakePaper()

    monkeypatch.setattr("wikify.store.db.get_session", lambda: FakeSession())

    result = ingest_markdown(md_file)
    assert result == 0
