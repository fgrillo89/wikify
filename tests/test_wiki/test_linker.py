"""Tests for the wiki cross-linker module."""

from __future__ import annotations

from pathlib import Path

from wikify.wiki.builder import generate_wiki_index, write_article
from wikify.wiki.linker import (
    _slug_to_title,
    cross_link_articles,
    ensure_parent_backlinks,
)
from wikify.wiki.sitemap import SitemapEntry, WikiSitemap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_article(
    wiki_dir: Path,
    subdir: str,
    slug: str,
    title: str,
    content: str,
    sources: list[str] | None = None,
    topics: list[str] | None = None,
    status: str = "full",
) -> Path:
    path = wiki_dir / subdir / f"{slug}.md"
    write_article(
        path=path,
        title=title,
        content=content,
        sources=sources or [],
        topics=topics or [],
        status=status,
        model="test-model",
    )
    return path


# ---------------------------------------------------------------------------
# _slug_to_title
# ---------------------------------------------------------------------------


def test_slug_to_title_reads_frontmatter(tmp_path):
    _make_article(tmp_path, "concepts", "hafnium_oxide", "Hafnium Oxide", "Body text.")
    _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "More body.")

    mapping = _slug_to_title(tmp_path)

    assert mapping["hafnium_oxide"] == "Hafnium Oxide"
    assert mapping["ald_basics"] == "ALD Basics"


def test_slug_to_title_skips_index_files(tmp_path):
    _make_article(tmp_path, "concepts", "real_article", "Real Article", "Body.")
    # Create a pseudo-index file.
    (tmp_path / "_index.md").write_text("---\ntitle: Index\n---\n", encoding="utf-8")

    mapping = _slug_to_title(tmp_path)

    assert "real_article" in mapping
    assert "_index" not in mapping


def test_slug_to_title_empty_dir(tmp_path):
    assert _slug_to_title(tmp_path) == {}


def test_slug_to_title_falls_back_to_stem(tmp_path):
    """If frontmatter has no title, falls back to filename stem."""
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "no_title.md").write_text(
        "# Just a heading\n\nNo frontmatter.",
        encoding="utf-8",
    )

    mapping = _slug_to_title(tmp_path)
    assert mapping.get("no_title") == "no_title"


# ---------------------------------------------------------------------------
# cross_link_articles — sitemap mode
# ---------------------------------------------------------------------------


def _make_sitemap(entries: list[SitemapEntry]) -> WikiSitemap:
    return WikiSitemap(entries=entries)


def test_cross_link_with_sitemap(tmp_path):
    art_a = _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "Introduction to ALD.")
    art_b = _make_article(tmp_path, "concepts", "hafnium_oxide", "Hafnium Oxide", "HfO2 details.")

    sitemap = _make_sitemap(
        [
            SitemapEntry(
                title="ALD Basics",
                slug="ald_basics",
                category="concept",
                scope="ALD fundamentals",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=["hafnium_oxide"],
                depth="full",
                source_types=["paper"],
            ),
            SitemapEntry(
                title="Hafnium Oxide",
                slug="hafnium_oxide",
                category="concept",
                scope="HfO2 properties",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=["ald_basics"],
                depth="full",
                source_types=["paper"],
            ),
        ]
    )

    updated = cross_link_articles(tmp_path, sitemap)

    assert updated == 2

    text_a = art_a.read_text(encoding="utf-8")
    assert "## See Also" in text_a
    assert "[[Hafnium Oxide]]" in text_a

    text_b = art_b.read_text(encoding="utf-8")
    assert "## See Also" in text_b
    assert "[[ALD Basics]]" in text_b


def test_cross_link_does_not_duplicate(tmp_path):
    """Running cross_link twice must not produce duplicate bullets."""
    art = _make_article(
        tmp_path,
        "concepts",
        "ald_basics",
        "ALD Basics",
        "Introduction.",
    )
    _make_article(tmp_path, "concepts", "hafnium_oxide", "Hafnium Oxide", "HfO2.")

    sitemap = _make_sitemap(
        [
            SitemapEntry(
                title="ALD Basics",
                slug="ald_basics",
                category="concept",
                scope="",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=["hafnium_oxide"],
                depth="full",
                source_types=[],
            ),
            SitemapEntry(
                title="Hafnium Oxide",
                slug="hafnium_oxide",
                category="concept",
                scope="",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
        ]
    )

    cross_link_articles(tmp_path, sitemap)
    cross_link_articles(tmp_path, sitemap)

    text = art.read_text(encoding="utf-8")
    assert text.count("[[Hafnium Oxide]]") == 1


# ---------------------------------------------------------------------------
# cross_link_articles — slug-matching fallback (sitemap=None)
# ---------------------------------------------------------------------------


def test_cross_link_slug_fallback(tmp_path):
    art_a = _make_article(
        tmp_path,
        "concepts",
        "ald_basics",
        "ALD Basics",
        "ALD Basics uses Thin Film deposition as its core method.",
    )
    art_b = _make_article(
        tmp_path,
        "concepts",
        "thin_film",
        "Thin Film",
        "Thin Film deposition is used widely in semiconductor processing.",
    )

    updated = cross_link_articles(tmp_path, sitemap=None)

    # art_a contains "Thin Film" verbatim -> should get a See Also for Thin Film.
    text_a = art_a.read_text(encoding="utf-8")
    assert "[[Thin Film]]" in text_a

    assert updated >= 1


def test_cross_link_no_false_links(tmp_path):
    """Articles with no title overlap should not get See Also links."""
    _make_article(tmp_path, "concepts", "topic_x", "Topic X", "Content about X.")
    _make_article(tmp_path, "concepts", "topic_y", "Topic Y", "Content about Y.")

    updated = cross_link_articles(tmp_path, sitemap=None)

    assert updated == 0


# ---------------------------------------------------------------------------
# cross_link inserts before ## References
# ---------------------------------------------------------------------------


def test_cross_link_before_references(tmp_path):
    art_a = _make_article(
        tmp_path,
        "concepts",
        "ald_basics",
        "ALD Basics",
        "Introduction.\n\n## References\n\n- [1] Smith 2020",
    )
    _make_article(tmp_path, "concepts", "hafnium_oxide", "Hafnium Oxide", "HfO2.")

    sitemap = _make_sitemap(
        [
            SitemapEntry(
                title="ALD Basics",
                slug="ald_basics",
                category="concept",
                scope="",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=["hafnium_oxide"],
                depth="full",
                source_types=[],
            ),
            SitemapEntry(
                title="Hafnium Oxide",
                slug="hafnium_oxide",
                category="concept",
                scope="",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
        ]
    )

    cross_link_articles(tmp_path, sitemap)

    text = art_a.read_text(encoding="utf-8")
    see_also_pos = text.index("## See Also")
    references_pos = text.index("## References")
    assert see_also_pos < references_pos


# ---------------------------------------------------------------------------
# ensure_parent_backlinks
# ---------------------------------------------------------------------------


def test_ensure_parent_backlinks_adds_section(tmp_path):
    theme_path = _make_article(
        tmp_path,
        "themes",
        "ald_theme",
        "ALD Theme",
        "This theme covers ALD.",
    )
    _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "Basics body.")

    sitemap = _make_sitemap(
        [
            SitemapEntry(
                title="ALD Theme",
                slug="ald_theme",
                category="theme",
                scope="ALD overview",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
            SitemapEntry(
                title="ALD Basics",
                slug="ald_basics",
                category="concept",
                scope="",
                parent_slug="ald_theme",
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
        ]
    )

    ensure_parent_backlinks(tmp_path, sitemap)

    text = theme_path.read_text(encoding="utf-8")
    assert "[[ALD Basics]]" in text


def test_ensure_parent_backlinks_no_duplicate(tmp_path):
    theme_path = _make_article(
        tmp_path,
        "themes",
        "ald_theme",
        "ALD Theme",
        "This theme covers ALD.\n\n## Concepts\n\n- [[ALD Basics]]\n",
    )
    _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "Basics body.")

    sitemap = _make_sitemap(
        [
            SitemapEntry(
                title="ALD Theme",
                slug="ald_theme",
                category="theme",
                scope="",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
            SitemapEntry(
                title="ALD Basics",
                slug="ald_basics",
                category="concept",
                scope="",
                parent_slug="ald_theme",
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
        ]
    )

    ensure_parent_backlinks(tmp_path, sitemap)

    text = theme_path.read_text(encoding="utf-8")
    assert text.count("[[ALD Basics]]") == 1


# ---------------------------------------------------------------------------
# generate_wiki_index (richer index)
# ---------------------------------------------------------------------------


def test_generate_wiki_index_has_required_headers(tmp_path):
    _make_article(tmp_path, "themes", "ald_theme", "ALD Theme", "Theme content.", status="full")
    _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "Concept body.", status="full")
    _make_article(
        tmp_path, "syntheses", "ald_synthesis", "ALD Synthesis", "Synthesis body.", status="full"
    )

    result = generate_wiki_index(tmp_path)

    assert "# Knowledge Base Index" in result
    assert "_Last updated:" in result
    assert "_Articles:" in result
    assert "Sources indexed:" in result
    assert "## Themes" in result
    assert "## Concepts" in result
    assert "## Syntheses" in result


def test_generate_wiki_index_writes_file(tmp_path):
    _make_article(tmp_path, "concepts", "ald_basics", "ALD Basics", "Body.")

    generate_wiki_index(tmp_path)

    index_path = tmp_path / "_index.md"
    assert index_path.exists()
    assert "ALD Basics" in index_path.read_text(encoding="utf-8")


def test_generate_wiki_index_counts_sources(tmp_path):
    _make_article(
        tmp_path,
        "concepts",
        "article_a",
        "Article A",
        "Body.",
        sources=["src1", "src2"],
    )
    _make_article(
        tmp_path,
        "concepts",
        "article_b",
        "Article B",
        "Body.",
        sources=["src2", "src3"],
    )

    result = generate_wiki_index(tmp_path)

    # 3 unique source IDs: src1, src2, src3
    assert "Sources indexed: 3" in result


def test_generate_wiki_index_empty_dir(tmp_path):
    result = generate_wiki_index(tmp_path)
    assert "# Knowledge Base Index" in result
    assert "Articles: 0" in result


def test_generate_wiki_index_recent_updates(tmp_path):
    _make_article(tmp_path, "concepts", "old_article", "Old Article", "Body.", status="stub")

    result = generate_wiki_index(tmp_path)

    assert "## Recent Updates" in result
    assert "Old Article" in result
