"""Tests for the HTML ingester."""

from __future__ import annotations

from scholarforge.ingest.html import (
    _extract_author,
    _extract_title,
    _extract_year,
    _strip_html_fallback,
    parse_html,
)

# ── Title extraction ──────────────────────────────────────────────────────────


def test_extract_title_from_title_tag():
    html = "<html><head><title>My Article</title></head><body></body></html>"
    assert _extract_title(html) == "My Article"


def test_extract_title_from_h1():
    html = "<html><body><h1>Section Header</h1><p>Body text.</p></body></html>"
    result = _extract_title(html)
    assert result == "Section Header"


def test_extract_title_empty():
    html = "<html><body><p>No heading here.</p></body></html>"
    assert _extract_title(html) == ""


def test_extract_title_prefers_title_tag():
    html = "<html><head><title>Page Title</title></head><body><h1>H1 Title</h1></body></html>"
    assert _extract_title(html) == "Page Title"


# ── Year extraction ───────────────────────────────────────────────────────────


def test_extract_year_from_published_time():
    html = (
        "<html><head>"
        '<meta property="article:published_time" content="2023-05-15T10:00:00Z"/>'
        "</head></html>"
    )
    assert _extract_year(html) == 2023


def test_extract_year_from_date_meta():
    html = '<html><head><meta name="date" content="2021-01-20"/></head></html>'
    assert _extract_year(html) == 2021


def test_extract_year_none_when_missing():
    html = "<html><body>No date here.</body></html>"
    assert _extract_year(html) is None


# ── Author extraction ─────────────────────────────────────────────────────────


def test_extract_author_from_meta():
    html = '<html><head><meta name="author" content="Jane Doe"/></head></html>'
    assert _extract_author(html) == ["Jane Doe"]


def test_extract_author_multiple_comma():
    html = '<html><head><meta name="author" content="Jane Doe, John Smith"/></head></html>'
    result = _extract_author(html)
    assert "Jane Doe" in result
    assert "John Smith" in result


def test_extract_author_empty():
    html = "<html><body>No author.</body></html>"
    assert _extract_author(html) == []


# ── HTML stripping fallback ───────────────────────────────────────────────────


def test_strip_html_fallback_removes_tags():
    html = "<p>Hello <b>world</b>!</p>"
    result = _strip_html_fallback(html)
    assert "<" not in result
    assert "Hello" in result
    assert "world" in result


def test_strip_html_fallback_removes_scripts():
    html = "<html><head><script>alert('hi');</script></head><body><p>Content</p></body></html>"
    result = _strip_html_fallback(html)
    assert "alert" not in result
    assert "Content" in result


# ── parse_html integration ────────────────────────────────────────────────────


def test_parse_html_basic(tmp_path, monkeypatch):
    """parse_html returns a Paper with correct metadata."""
    monkeypatch.setattr(
        "scholarforge.ingest.html.chunk_sections",
        lambda text, tree, pid: [],
    )

    html_file = tmp_path / "article.html"
    html_file.write_text(
        """<html>
<head>
<title>ALD for Memristors</title>
<meta name="author" content="Dr. Grillo"/>
<meta property="article:published_time" content="2024-03-01"/>
<meta name="description" content="Overview of ALD in memristor fabrication."/>
</head>
<body>
<h1>ALD for Memristors</h1>
<p>Atomic layer deposition enables precise thin-film control.</p>
</body>
</html>""",
        encoding="utf-8",
    )

    paper, chunks = parse_html(html_file)

    assert paper.title == "ALD for Memristors"
    assert paper.year == 2024
    assert "Dr. Grillo" in paper.authors
    assert paper.doc_type == "web_article"
    assert paper.source_path == str(html_file)


def test_parse_html_fallback_title(tmp_path, monkeypatch):
    """parse_html falls back to stem when no title found."""
    monkeypatch.setattr(
        "scholarforge.ingest.html.chunk_sections",
        lambda text, tree, pid: [],
    )

    html_file = tmp_path / "my_clip.html"
    html_file.write_text(
        "<html><body><p>Content without title.</p></body></html>", encoding="utf-8"
    )

    paper, chunks = parse_html(html_file)
    assert paper.title == "my_clip"
    assert paper.doc_type == "web_article"


def test_ingest_html_skips_existing(tmp_path, monkeypatch):
    """ingest_html returns 0 when paper already in DB."""
    from scholarforge.ingest.html import ingest_html

    html_file = tmp_path / "dup.html"
    html_file.write_text("<html><body><p>Duplicate.</p></body></html>", encoding="utf-8")

    class FakePaper:
        pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, model, key):
            return FakePaper()

    monkeypatch.setattr("scholarforge.store.db.get_session", lambda: FakeSession())

    result = ingest_html(html_file)
    assert result == 0
