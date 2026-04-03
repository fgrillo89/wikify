"""Integration tests for the 'scholarforge wiki' CLI commands.

Uses Typer's CliRunner and unittest.mock.patch. No real LLM or DB calls.
All tests patch at the module boundary so no real DB, LLM, or filesystem
touches are needed (except for the query tests that build a local wiki dir).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from scholarforge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_sitemap() -> "WikiSitemap":  # noqa: F821
    """Build a minimal in-memory WikiSitemap (does not touch disk)."""
    from scholarforge.wiki.sitemap import SitemapEntry, WikiSitemap

    entries = [
        SitemapEntry(
            title="ALD Fundamentals",
            slug="ald_fundamentals",
            category="theme",
            scope="Covers the basics of atomic layer deposition.",
            parent_slug=None,
            key_source_ids=[],
            related_slugs=["ald_materials"],
            depth="full",
            source_types=["paper"],
        ),
        SitemapEntry(
            title="ALD Materials",
            slug="ald_materials",
            category="concept",
            scope="Common precursor materials used in ALD.",
            parent_slug="ald_fundamentals",
            key_source_ids=[],
            related_slugs=["ald_fundamentals"],
            depth="draft",
            source_types=["paper"],
        ),
    ]
    return WikiSitemap(entries=entries, corpus_summary="test corpus", model="test-model")


def _noop_generate_wiki_index(wiki_dir: Path) -> str:
    """Replacement for generate_wiki_index that creates the file without LLM calls."""
    index = wiki_dir / "_index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("# Knowledge Base Index\n", encoding="utf-8")
    return "# Knowledge Base Index\n"


# ---------------------------------------------------------------------------
# wiki init
# ---------------------------------------------------------------------------


class TestWikiInit:
    """Tests for 'scholarforge wiki init'."""

    def test_init_calls_generate_sitemap_and_build(self, tmp_path: Path) -> None:
        fake_sitemap = _make_fake_sitemap()

        with (
            patch(
                "scholarforge.wiki.sitemap.generate_sitemap",
                return_value=fake_sitemap,
            ) as mock_gen,
            patch(
                "scholarforge.wiki.agent.build_wiki_from_sitemap",
                return_value=[],
            ) as mock_build,
            patch("scholarforge.wiki.linker.cross_link_articles", return_value=2),
            patch("scholarforge.wiki.linker.ensure_parent_backlinks"),
            patch(
                "scholarforge.wiki.builder.generate_wiki_index",
                side_effect=_noop_generate_wiki_index,
            ),
            # Redirect wiki_dir to tmp_path so index file writes succeed
            patch("scholarforge.cli.Path", new=lambda p: tmp_path if p == "data/wiki" else Path(p)),
        ):
            result = runner.invoke(app, ["wiki", "init", "--topic", "ALD"])

        assert result.exit_code == 0, result.output
        mock_gen.assert_called_once()
        mock_build.assert_called_once()
        # Summary should mention planned article counts
        assert "planned" in result.output.lower() or "themes" in result.output.lower()

    def test_init_prints_summary_counts(self, tmp_path: Path) -> None:
        from scholarforge.wiki.sitemap import SitemapEntry, WikiSitemap

        entries = [
            SitemapEntry(
                title="Theme A",
                slug="theme_a",
                category="theme",
                scope="Theme.",
                parent_slug=None,
                key_source_ids=[],
                related_slugs=[],
                depth="full",
                source_types=[],
            ),
            SitemapEntry(
                title="Concept B",
                slug="concept_b",
                category="concept",
                scope="Concept.",
                parent_slug="theme_a",
                key_source_ids=[],
                related_slugs=[],
                depth="stub",
                source_types=[],
            ),
        ]
        fake_sitemap = WikiSitemap(entries=entries)

        with (
            patch(
                "scholarforge.wiki.sitemap.generate_sitemap",
                return_value=fake_sitemap,
            ),
            patch("scholarforge.wiki.agent.build_wiki_from_sitemap", return_value=[]),
            patch("scholarforge.wiki.linker.cross_link_articles", return_value=1),
            patch("scholarforge.wiki.linker.ensure_parent_backlinks"),
            patch(
                "scholarforge.wiki.builder.generate_wiki_index",
                side_effect=_noop_generate_wiki_index,
            ),
            patch(
                "scholarforge.cli.Path",
                new=lambda p: tmp_path if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "init"])

        assert result.exit_code == 0, result.output
        # Summary line should contain the total article count (2)
        assert "2" in result.output


# ---------------------------------------------------------------------------
# wiki expand
# ---------------------------------------------------------------------------


class TestWikiExpand:
    """Tests for 'scholarforge wiki expand'."""

    def test_expand_concept_writes_article(self) -> None:
        """Expand a concept via the no-sitemap fallback path."""
        fake_content = "## Overview\n\nThis is the ALD article."
        fake_source_ids = ["abc123"]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("scholarforge.wiki.sitemap.WikiSitemap.load", return_value=None),
            patch(
                "scholarforge.wiki.agent.build_wiki_article",
                return_value=(fake_content, fake_source_ids),
            ) as mock_bwa,
            patch("scholarforge.wiki.builder.write_article") as mock_write,
            patch("scholarforge.wiki.linker.cross_link_articles", return_value=0),
            patch("scholarforge.wiki.builder.generate_wiki_index", return_value=""),
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
        ):
            result = runner.invoke(
                app,
                ["wiki", "expand", "atomic layer deposition"],
            )

        assert result.exit_code == 0, result.output
        mock_bwa.assert_called_once()
        mock_write.assert_called_once()

    def test_expand_no_sitemap_no_concept_exits_nonzero(self) -> None:
        with patch("scholarforge.wiki.sitemap.WikiSitemap.load", return_value=None):
            result = runner.invoke(app, ["wiki", "expand"])
        assert result.exit_code != 0

    def test_expand_all_from_sitemap(self, tmp_path: Path) -> None:
        from scholarforge.wiki.sitemap import SitemapEntry, WikiSitemap

        stub_entry = SitemapEntry(
            title="Stub Topic",
            slug="stub_topic",
            category="concept",
            scope="A stub.",
            parent_slug=None,
            key_source_ids=[],
            related_slugs=[],
            depth="stub",
            source_types=[],
        )
        fake_sitemap = WikiSitemap(entries=[stub_entry])
        stub_path = tmp_path / "stub_topic.md"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.first.return_value = None

        with (
            patch("scholarforge.wiki.sitemap.WikiSitemap.load", return_value=fake_sitemap),
            patch(
                "scholarforge.wiki.agent.build_article_from_entry",
                return_value=("body text", []),
            ),
            patch("scholarforge.wiki.builder.write_article"),
            patch("scholarforge.wiki.builder.article_path", return_value=stub_path),
            patch("scholarforge.wiki.linker.cross_link_articles", return_value=0),
            patch("scholarforge.wiki.builder.generate_wiki_index", return_value=""),
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
        ):
            result = runner.invoke(app, ["wiki", "expand", "--all"])

        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# wiki sync
# ---------------------------------------------------------------------------


class TestWikiSync:
    """Tests for 'scholarforge wiki sync'."""

    def test_sync_no_stale_articles(self) -> None:
        """When no articles have needs_update=True, prints 'Synced 0 articles'."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = []

        with (
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
            patch("scholarforge.wiki.builder.generate_wiki_index", return_value=""),
        ):
            result = runner.invoke(app, ["wiki", "sync"])

        assert result.exit_code == 0, result.output
        assert "Synced 0 articles" in result.output

    def test_sync_one_stale_article(self, tmp_path: Path) -> None:
        """Syncs a single stale article: updates file and clears needs_update."""
        now = datetime.now(timezone.utc)

        article_file = tmp_path / "concepts" / "test_article.md"
        article_file.parent.mkdir(parents=True)
        article_file.write_text("---\ntitle: Test\n---\n\nOriginal body.\n", encoding="utf-8")

        from scholarforge.store.models import WikiArticle

        stale_row = WikiArticle(
            id="test_article",
            title="Test Article",
            status="full",
            file_path=str(article_file),
            source_ids=json.dumps(["src1"]),
            topic_keys=json.dumps(["test"]),
            created_at=now,
            updated_at=now,
            model="test",
            needs_update=True,
        )

        # First Session call: return stale articles list
        mock_stale_session = MagicMock()
        mock_stale_session.__enter__ = MagicMock(return_value=mock_stale_session)
        mock_stale_session.__exit__ = MagicMock(return_value=False)
        mock_stale_session.exec.return_value.all.return_value = [stale_row]

        # Second Session call: update the row
        mock_update_session = MagicMock()
        mock_update_session.__enter__ = MagicMock(return_value=mock_update_session)
        mock_update_session.__exit__ = MagicMock(return_value=False)
        mock_update_session.exec.return_value.first.return_value = stale_row

        session_calls = iter([mock_stale_session, mock_update_session])

        def _session_factory(_engine):
            try:
                return next(session_calls)
            except StopIteration:
                return mock_update_session

        with (
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", side_effect=_session_factory),
            patch(
                "scholarforge.agent.tools.read_paper_digest",
                return_value="digest text",
            ),
            patch(
                "scholarforge.wiki.agent.update_wiki_article",
                return_value="Updated body.",
            ),
            patch("scholarforge.wiki.builder.write_article"),
            patch("scholarforge.wiki.builder.generate_wiki_index", return_value=""),
        ):
            result = runner.invoke(app, ["wiki", "sync"])

        assert result.exit_code == 0, result.output
        assert "Synced 1 articles" in result.output


# ---------------------------------------------------------------------------
# wiki health
# ---------------------------------------------------------------------------


class TestWikiHealth:
    """Tests for 'scholarforge wiki health'."""

    def test_health_no_db_does_not_crash(self, tmp_path: Path) -> None:
        """Health command gracefully handles an empty or missing DB."""
        with (
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", side_effect=Exception("No DB")),
            patch(
                "scholarforge.agent.tools.find_synthesis_opportunities",
                return_value="",
            ),
            # Redirect wiki_dir so the _health.md write succeeds
            patch(
                "scholarforge.cli.Path",
                new=lambda p: tmp_path if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "health"])

        assert result.exit_code == 0, result.output
        assert "Wiki Health Report" in result.output

    def test_health_prints_counts(self, tmp_path: Path) -> None:
        """Health prints expected counts when DB is present."""
        now = datetime.now(timezone.utc)

        from scholarforge.store.models import WikiArticle

        rows = [
            WikiArticle(
                id="article_a",
                title="Article A",
                status="full",
                file_path="concepts/article_a.md",
                source_ids=json.dumps(["s1"]),
                topic_keys=json.dumps(["a"]),
                created_at=now,
                updated_at=now,
                model="test",
                needs_update=False,
            ),
        ]

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = rows

        with (
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
            patch(
                "scholarforge.agent.tools.find_synthesis_opportunities",
                return_value="",
            ),
            patch(
                "scholarforge.cli.Path",
                new=lambda p: tmp_path if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "health"])

        assert result.exit_code == 0, result.output
        assert "Total articles: 1" in result.output


# ---------------------------------------------------------------------------
# wiki query
# ---------------------------------------------------------------------------


class TestWikiQuery:
    """Tests for 'scholarforge wiki query'."""

    def test_query_prints_answer(self, tmp_path: Path) -> None:
        """Query returns an LLM-generated answer from wiki articles."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_file = wiki_dir / "_index.md"
        index_file.write_text("# Knowledge Base Index\n\n## Concepts\n\n- [[ALD Fundamentals]]\n")

        concepts_dir = wiki_dir / "concepts"
        concepts_dir.mkdir()
        (concepts_dir / "ald_fundamentals.md").write_text(
            "---\ntitle: ALD Fundamentals\n---\n\nALD is a thin-film deposition technique.\n"
        )

        llm_responses = iter(
            [
                "ald_fundamentals",  # relevance call
                "ALD is atomic layer deposition, a technique for thin films.",  # answer
            ]
        )

        with (
            patch(
                "scholarforge.llm.client.complete",
                side_effect=lambda **kw: next(llm_responses),
            ),
            patch(
                "scholarforge.cli.Path",
                new=lambda p: wiki_dir if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD?"])

        assert result.exit_code == 0, result.output
        assert "ALD" in result.output

    def test_query_no_index_exits_nonzero(self, tmp_path: Path) -> None:
        """Query exits with error when no _index.md exists."""
        empty_wiki = tmp_path / "wiki_empty"
        empty_wiki.mkdir()

        with patch(
            "scholarforge.cli.Path",
            new=lambda p: empty_wiki if p == "data/wiki" else Path(p),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD?"])

        assert result.exit_code != 0

    def test_query_promote_writes_db_row(self, tmp_path: Path) -> None:
        """--promote creates a WikiArticle DB row after answering."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "_index.md").write_text("# Knowledge Base Index\n")

        llm_responses = iter(
            [
                "ald_overview",  # relevance slug
                "ALD is great for thin films.",  # answer
            ]
        )

        mock_sess = MagicMock()
        mock_sess.__enter__ = MagicMock(return_value=mock_sess)
        mock_sess.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "scholarforge.llm.client.complete",
                side_effect=lambda **kw: next(llm_responses),
            ),
            patch(
                "scholarforge.cli.Path",
                new=lambda p: wiki_dir if p == "data/wiki" else Path(p),
            ),
            patch("scholarforge.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_sess),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD overview?", "--promote"])

        assert result.exit_code == 0, result.output
        assert "promoted" in result.output.lower() or "queries" in result.output.lower()
        # DB merge was called
        mock_sess.merge.assert_called_once()
