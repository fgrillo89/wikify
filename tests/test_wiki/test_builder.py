"""Tests for the wiki builder module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wikify.wiki.builder import (
    article_path,
    find_stale_articles,
    generate_wiki_index,
    read_article_frontmatter,
    slugify,
    write_article,
)

# ── slugify ───────────────────────────────────────────────────────────────────


def test_slugify_basic():
    assert slugify("Hafnium Oxide") == "hafnium_oxide"


def test_slugify_special_chars():
    assert slugify("HfO2 ALD: Memristors") == "hfo2_ald_memristors"


def test_slugify_already_slug():
    assert slugify("simple_slug") == "simple_slug"


def test_slugify_multiple_spaces():
    result = slugify("  Multiple   Spaces  ")
    assert " " not in result
    assert result == "multiple_spaces"


# ── article_path ──────────────────────────────────────────────────────────────


def test_article_path_returns_correct_path(tmp_path):
    path = article_path(tmp_path, "concepts", "hafnium_oxide")
    assert path == tmp_path / "concepts" / "hafnium_oxide.md"


def test_article_path_different_categories(tmp_path):
    assert article_path(tmp_path, "syntheses", "ald_review").name == "ald_review.md"
    assert article_path(tmp_path, "gaps", "missing_topic").parent.name == "gaps"


# ── write_article and read_article_frontmatter ────────────────────────────────


def test_write_article_creates_file(tmp_path):
    path = tmp_path / "concepts" / "test_concept.md"
    write_article(
        path=path,
        title="Test Concept",
        content="This is the article body.",
        sources=["abc123", "def456"],
        topics=["concept", "test"],
        status="full",
        model="claude-test",
    )
    assert path.exists()


def test_write_article_has_frontmatter(tmp_path):
    path = tmp_path / "test.md"
    write_article(
        path=path,
        title="ALD Basics",
        content="ALD is a deposition technique.",
        sources=["aaa"],
        topics=["ALD"],
        status="draft",
        model="",
    )
    text = path.read_text(encoding="utf-8")
    assert "---" in text
    assert "title: ALD Basics" in text
    assert "status: draft" in text


def test_write_article_contains_body(tmp_path):
    path = tmp_path / "body_test.md"
    write_article(
        path=path,
        title="Body Test",
        content="The body content is here.",
        sources=[],
        topics=[],
        status="stub",
        model="",
    )
    text = path.read_text(encoding="utf-8")
    assert "The body content is here." in text


def test_read_article_frontmatter_round_trip(tmp_path):
    path = tmp_path / "round_trip.md"
    write_article(
        path=path,
        title="Round Trip Test",
        content="Content.",
        sources=["s1"],
        topics=["topic1"],
        status="full",
        model="claude-3",
    )
    meta = read_article_frontmatter(path)
    assert meta.get("title") == "Round Trip Test"
    assert meta.get("status") == "full"
    assert meta.get("model") == "claude-3"


def test_read_article_frontmatter_missing_file(tmp_path):
    path = tmp_path / "nonexistent.md"
    assert read_article_frontmatter(path) == {}


def test_write_article_creates_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "nested" / "dir" / "article.md"
    write_article(
        path=path,
        title="Nested",
        content="Content.",
        sources=[],
        topics=[],
        status="stub",
        model="",
    )
    assert path.exists()


# ── find_stale_articles ───────────────────────────────────────────────────────


class _MockArticle:
    def __init__(self, slug: str, updated_at: datetime):
        self.id = slug
        self.title = slug
        self.updated_at = updated_at


def test_find_stale_articles_returns_old_ones():
    now = datetime.now(timezone.utc)
    old = _MockArticle("old", now - timedelta(days=60))
    recent = _MockArticle("recent", now - timedelta(days=1))

    stale = find_stale_articles([old, recent], cutoff=now - timedelta(days=30))
    assert old in stale
    assert recent not in stale


def test_find_stale_articles_empty_list():
    now = datetime.now(timezone.utc)
    assert find_stale_articles([], cutoff=now) == []


def test_find_stale_articles_all_fresh():
    now = datetime.now(timezone.utc)
    articles = [
        _MockArticle("a", now - timedelta(hours=1)),
        _MockArticle("b", now - timedelta(hours=2)),
    ]
    stale = find_stale_articles(articles, cutoff=now - timedelta(days=7))
    assert stale == []


def test_find_stale_articles_handles_naive_datetime():
    """Naive datetimes (no tzinfo) should be treated as UTC."""
    now = datetime.now(timezone.utc)
    naive_old = _MockArticle("naive_old", datetime(2020, 1, 1))  # Very old, no tz
    stale = find_stale_articles([naive_old], cutoff=now - timedelta(days=1))
    assert naive_old in stale


# ── generate_wiki_index ───────────────────────────────────────────────────────


def test_generate_wiki_index_empty_dir(tmp_path):
    result = generate_wiki_index(tmp_path)
    assert "# Knowledge Base Index" in result


def test_generate_wiki_index_with_articles(tmp_path):
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir()

    art = concepts_dir / "hafnium_oxide.md"
    write_article(
        path=art,
        title="Hafnium Oxide",
        content="HfO2 is a high-k dielectric.",
        sources=[],
        topics=["HfO2", "dielectric"],
        status="full",
        model="",
    )

    result = generate_wiki_index(tmp_path)
    assert "Hafnium Oxide" in result
    assert "concepts" in result.lower()


# ── resolve_article_sources ─────────────────────────────────────────────────


def test_resolve_article_sources_updates_frontmatter(tmp_path):
    """REF markers are resolved to paper IDs in frontmatter."""
    from unittest.mock import MagicMock, patch

    from wikify.wiki.builder import resolve_article_sources

    # Write an article with a [REF:] marker
    article = tmp_path / "test.md"
    article.write_text(
        "---\ntitle: Test\nsources:\n  []\n---\n\nALD is a technique [REF:Yang 2011].\n",
        encoding="utf-8",
    )

    # Mock Paper with matching display name
    mock_paper = MagicMock()
    mock_paper.id = "abc123"
    mock_paper.display_name.return_value = "Yang 2011 - Dopant Control"
    mock_paper.parsed_authors = ["Yang"]
    mock_paper.year = 2011

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    exec_result = MagicMock()
    exec_result.all.return_value = [mock_paper]
    mock_session.exec.return_value = exec_result

    with patch("wikify.store.db.get_session", return_value=mock_session):
        resolved = resolve_article_sources(article)

    assert len(resolved) == 1
    assert "abc123" in resolved

    # Check frontmatter was updated
    content = article.read_text(encoding="utf-8")
    assert "abc123" in content
    assert "[]" not in content
