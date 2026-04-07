"""Integration tests for the 'wikify wiki' CLI commands.

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

from wikify.cli import app
from wikify.core.store.models import EpochLog

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_sitemap() -> "WikiSitemap":  # noqa: F821
    """Build a minimal in-memory WikiSitemap (does not touch disk)."""
    from wikify.wiki.sitemap_data import SitemapEntry, WikiSitemap

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


class TestWikiSync:
    """Tests for 'wikify wiki sync'."""

    def test_sync_no_stale_articles(self) -> None:
        """When no articles have needs_update=True, prints 'Synced 0 articles'."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = []

        with (
            patch("wikify.core.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
            patch("wikify.wiki.builder.generate_wiki_index", return_value=""),
        ):
            result = runner.invoke(app, ["wiki", "sync"])

        assert result.exit_code == 0, result.output
        assert "Synced 0 articles" in result.output

    def test_sync_one_stale_article(self, tmp_path: Path) -> None:
        """Syncs a single stale article via the new map-reduce + maintenance pipeline."""
        now = datetime.now(timezone.utc)

        article_file = tmp_path / "concepts" / "test_article.md"
        article_file.parent.mkdir(parents=True)
        article_file.write_text(
            "---\ntitle: Test\n---\n## Established\n\nOriginal body.\n", encoding="utf-8"
        )

        from wikify.core.store.models import WikiArticle

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
            domain="test_domain",
        )

        from wikify.wiki.mapreduce import SourceExtraction

        mock_ext = SourceExtraction(
            source_id="src1",
            display_name="Smith 2024 - Test",
            doc_type="paper",
            graph_role="standard",
            pagerank_score=0.0,
            extraction="YES: New finding.",
            is_relevant=True,
        )

        # Session mock that cycles through calls
        stale_exec = MagicMock()
        stale_exec.all.return_value = [stale_row]
        coverage_exec = MagicMock()
        coverage_exec.all.return_value = []  # no existing coverage
        update_exec = MagicMock()
        update_exec.first.return_value = stale_row

        session_mock = MagicMock()
        session_mock.__enter__ = lambda s: session_mock
        session_mock.__exit__ = MagicMock(return_value=False)

        call_index = [0]
        exec_results = [stale_exec, coverage_exec, update_exec]

        def exec_by_call(stmt):
            idx = call_index[0]
            call_index[0] += 1
            if idx < len(exec_results):
                return exec_results[idx]
            return MagicMock(all=lambda: [], first=lambda: None)

        session_mock.exec.side_effect = exec_by_call

        with (
            patch("wikify.core.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=session_mock),
            patch("wikify.wiki.mapreduce.map_chunks_to_topic", return_value=[mock_ext]),
            patch("wikify.wiki.maintenance.detect_contradiction", return_value=False),
            patch("wikify.wiki.maintenance.additive_update", return_value="Updated body."),
            patch("wikify.wiki.persona.get_or_create_persona", return_value="Persona text"),
            patch("wikify.wiki.builder.write_article"),
            patch("wikify.wiki.builder.generate_wiki_index", return_value=""),
        ):
            result = runner.invoke(app, ["wiki", "sync"])

        assert result.exit_code == 0, result.output
        assert "Synced 1 articles" in result.output


# ---------------------------------------------------------------------------
# wiki health
# ---------------------------------------------------------------------------


class TestWikiHealth:
    """Tests for 'wikify wiki health'."""

    def test_health_no_db_does_not_crash(self, tmp_path: Path) -> None:
        """Health command gracefully handles an empty or missing DB."""
        with (
            patch("wikify.core.store.db.get_engine"),
            patch("sqlmodel.Session", side_effect=Exception("No DB")),
            patch(
                "wikify.papers.agent.tools.find_synthesis_opportunities",
                return_value="",
            ),
            # Redirect wiki_dir so the _health.md write succeeds
            patch(
                "wikify.cli.wiki.Path",
                new=lambda p: tmp_path if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "health"])

        assert result.exit_code == 0, result.output
        assert "Wiki Health Report" in result.output

    def test_health_prints_counts(self, tmp_path: Path) -> None:
        """Health prints expected counts when DB is present."""
        now = datetime.now(timezone.utc)

        from wikify.core.store.models import WikiArticle

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
            patch("wikify.core.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=mock_session),
            patch(
                "wikify.papers.agent.tools.find_synthesis_opportunities",
                return_value="",
            ),
            patch(
                "wikify.cli.wiki.Path",
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
    """Tests for 'wikify wiki query'."""

    def test_query_prints_answer(self, tmp_path: Path) -> None:
        """Query returns an answer via the shared runtime."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_file = wiki_dir / "index.md"
        index_file.write_text("# Knowledge Base Index\n\n## Concepts\n\n- [[ALD Fundamentals]]\n")

        with (
            patch(
                "wikify.wiki.runtime.query_wiki",
                return_value={
                    "answered": True,
                    "answer": "ALD is atomic layer deposition, a technique for thin films.",
                    "promoted_path": "",
                },
            ),
            patch(
                "wikify.cli.wiki.Path",
                new=lambda p: wiki_dir if p == "data/wiki" else Path(p),
            ),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD?"])

        assert result.exit_code == 0, result.output
        assert "ALD" in result.output

    def test_query_no_index_exits_nonzero(self, tmp_path: Path) -> None:
        """Query exits with error when no visible wiki exists."""
        empty_wiki = tmp_path / "wiki_empty"
        empty_wiki.mkdir()

        with patch(
            "wikify.cli.wiki.Path",
            new=lambda p: empty_wiki if p == "data/wiki" else Path(p),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD?"])

        assert result.exit_code != 0

    def test_query_promote_reconciles_runtime_state(self, tmp_path: Path) -> None:
        """--promote reports the promoted path and reconciles visible state."""
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "index.md").write_text("# Knowledge Base Index\n")

        with (
            patch(
                "wikify.wiki.runtime.query_wiki",
                return_value={
                    "answered": True,
                    "answer": "ALD is great for thin films.",
                    "promoted_path": str(wiki_dir / "articles" / "ald-overview.md"),
                },
            ),
            patch(
                "wikify.cli.wiki.Path",
                new=lambda p: wiki_dir if p == "data/wiki" else Path(p),
            ),
            patch("wikify.wiki.runtime.reconcile_state", return_value={}),
        ):
            result = runner.invoke(app, ["wiki", "query", "what is ALD overview?", "--promote"])

        assert result.exit_code == 0, result.output
        assert "promoted" in result.output.lower() or "ald-overview" in result.output.lower()


# ---------------------------------------------------------------------------
# wiki runtime services
# ---------------------------------------------------------------------------


class TestWikiRuntimeCommands:
    def test_campaign_command_prints_summary(self) -> None:
        with patch(
            "wikify.wiki.runtime.run_campaign",
            return_value={
                "campaign_id": "ald-thesis",
                "epochs_run": 2,
                "answered": True,
                "promoted_path": "data/wiki/articles/ald-thesis.md",
                "answer": "The campaign answer.",
            },
        ) as mock_run_campaign:
            result = runner.invoke(
                app,
                [
                    "wiki",
                    "campaign",
                    "ALD thesis",
                    "--epochs",
                    "2",
                    "--allow-echo-extractor",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "ald-thesis" in result.output
        assert "The campaign answer." in result.output
        mock_run_campaign.assert_called_once_with(
            "ALD thesis",
            wiki_dir=Path("data/wiki"),
            name="",
            domain="",
            epochs=2,
            model=None,
            promote=True,
            allow_echo_extractor=True,
        )

    def test_maintain_command_prints_summary(self) -> None:
        with patch(
            "wikify.wiki.runtime.run_maintain",
            return_value={
                "pages_seen": 3,
                "findings": 5,
                "pages_updated": 2,
                "pages_created": 1,
                "pages_deleted": 0,
            },
        ):
            result = runner.invoke(app, ["wiki", "maintain"])

        assert result.exit_code == 0, result.output
        assert "Findings" in result.output
        assert "5" in result.output

    def test_reconcile_state_command_prints_summary(self) -> None:
        with patch(
            "wikify.wiki.runtime.reconcile_state",
            return_value={
                "pages_seen": 4,
                "pages_created": 1,
                "pages_updated": 3,
                "pages_deleted": 0,
            },
        ):
            result = runner.invoke(app, ["wiki", "reconcile-state"])

        assert result.exit_code == 0, result.output
        assert "Pages seen" in result.output
        assert "4" in result.output

    def test_export_metrics_command_prints_output_path(self) -> None:
        with patch(
            "wikify.wiki.runtime.export_metrics",
            return_value={"run_count": 2, "export_path": "data/wiki/_meta/metrics/export.json"},
        ):
            result = runner.invoke(app, ["wiki", "export-metrics"])

        assert result.exit_code == 0, result.output
        assert "export.json" in result.output


# ---------------------------------------------------------------------------
# wiki epoch
# ---------------------------------------------------------------------------


class TestWikiEpoch:
    def test_epoch_command_reads_epochlog_object(self) -> None:
        log = EpochLog(
            epoch=1,
            triggered_by="user",
            concepts_discovered=3,
            articles_written=2,
            stubs_upgraded=1,
            loss_score=0.2,
            loss_delta=0.05,
            converged=False,
        )

        with patch("wikify.wiki.epoch.run_epoch", return_value=log) as mock_run_epoch:
            result = runner.invoke(app, ["wiki", "epoch", "--allow-echo-extractor"])

        assert result.exit_code == 0, result.output
        assert "Concepts discovered : 3" in result.output
        assert "Articles written    : 2" in result.output
        mock_run_epoch.assert_called_once_with(
            triggered_by="user",
            domain="",
            model=None,
            allow_echo_extractor=True,
        )

    def test_epoch_until_convergence_reads_epochlog_list(self) -> None:
        logs = [
            EpochLog(epoch=1, triggered_by="schedule", loss_score=0.4, converged=False),
            EpochLog(
                epoch=2,
                triggered_by="schedule",
                articles_written=4,
                stubs_upgraded=2,
                loss_score=0.1,
                converged=True,
            ),
        ]

        with patch("wikify.wiki.epoch.run_until_convergence", return_value=logs):
            result = runner.invoke(app, ["wiki", "epoch", "--until-convergence", "--n", "2"])

        assert result.exit_code == 0, result.output
        assert "Converged after 2 epoch(s)" in result.output
        assert "Final loss : 0.1000" in result.output
