"""Tests for wiki audit CLI command and wiki sync routing.

Uses Typer's CliRunner and unittest.mock.patch.
No real LLM or DB calls.
All patches target the source module where the name is defined, because
CLI commands use lazy imports (import inside function body).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_wiki_dir(tmp_path: Path) -> Path:
    """Create a minimal wiki directory structure with an index."""
    wiki_dir = tmp_path / "data" / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "_index.md").write_text(
        "# Knowledge Base\n\n## Domains\n\n- [Test](domains/test/_index.md)\n",
        encoding="utf-8",
    )
    return wiki_dir


def _make_structural_report(domain=""):
    from wikify.wiki.maintenance import StructuralReport

    return StructuralReport(
        domain=domain,
        split_candidates=["slug_heavy"],
        merge_candidates=[("slug_a", "slug_b")],
        deprecation_candidates=["slug_empty"],
        orphan_sources=["paper_orphan_1"],
        contradiction_flags=["slug_conflict"],
        graph_drift=["Hub Paper 2021"],
    )


def _make_stale_article(
    article_id="test_article",
    title="Test Article",
    source_ids=None,
    domain="test",
    file_path=None,
):
    art = MagicMock()
    art.id = article_id
    art.title = title
    art.source_ids = json.dumps(source_ids or ["src_a", "src_b"])
    art.domain = domain
    art.file_path = file_path or f"wiki/concepts/{article_id}.md"
    art.needs_update = True
    art.status = "draft"
    art.model = "test-model"
    art.topic_keys = "[]"
    return art


def _make_session_ctx(exec_results: list):
    """Return a context-manager mock whose .exec() cycles through exec_results."""
    session_mock = MagicMock()
    session_mock.__enter__ = lambda s: session_mock
    session_mock.__exit__ = MagicMock(return_value=False)

    call_index = [0]

    def exec_by_call(stmt):
        idx = call_index[0]
        call_index[0] += 1
        if idx < len(exec_results):
            return exec_results[idx]
        return MagicMock(all=lambda: [], first=lambda: None)

    session_mock.exec.side_effect = exec_by_call
    return session_mock


# ── wiki audit tests ──────────────────────────────────────────────────────────


class TestWikiAudit:
    def test_audit_runs_without_error(self, tmp_path, monkeypatch):
        """wiki audit should run and print a report without errors."""
        _make_wiki_dir(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch("wikify.wiki.maintenance.structural_audit") as mock_audit:
            mock_audit.return_value = _make_structural_report()
            result = runner.invoke(app, ["wiki", "audit", "--domain", "test"])

        assert result.exit_code == 0, result.output

    def test_audit_writes_audit_md(self, tmp_path, monkeypatch):
        """wiki audit should create data/wiki/_audit.md."""
        _make_wiki_dir(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch("wikify.wiki.maintenance.structural_audit") as mock_audit:
            mock_audit.return_value = _make_structural_report()
            result = runner.invoke(app, ["wiki", "audit"])

        assert result.exit_code == 0, result.output
        audit_path = tmp_path / "data" / "wiki" / "_audit.md"
        assert audit_path.exists(), f"Expected {audit_path} to exist"
        content = audit_path.read_text(encoding="utf-8")
        assert "Wiki Structural Audit" in content

    def test_audit_report_contains_candidates(self, tmp_path, monkeypatch):
        """Audit report should list split, merge, deprecation, orphan, and contradiction items."""
        _make_wiki_dir(tmp_path)
        monkeypatch.chdir(tmp_path)

        with patch("wikify.wiki.maintenance.structural_audit") as mock_audit:
            mock_audit.return_value = _make_structural_report("mydom")
            result = runner.invoke(app, ["wiki", "audit", "--domain", "mydom"])

        assert result.exit_code == 0, result.output
        audit_path = tmp_path / "data" / "wiki" / "_audit.md"
        content = audit_path.read_text(encoding="utf-8")
        assert "slug_heavy" in content
        assert "slug_a" in content
        assert "slug_empty" in content
        assert "paper_orphan_1" in content
        assert "slug_conflict" in content
        assert "Hub Paper 2021" in content

    def test_audit_fix_sets_needs_update(self, tmp_path, monkeypatch):
        """wiki audit --fix should set needs_update=True on split/merge candidates."""
        _make_wiki_dir(tmp_path)
        monkeypatch.chdir(tmp_path)

        report = _make_structural_report()

        session_mock = MagicMock()
        session_mock.__enter__ = lambda s: session_mock
        session_mock.__exit__ = MagicMock(return_value=False)
        session_mock.exec.return_value = MagicMock(first=lambda: MagicMock())

        with (
            patch("wikify.wiki.maintenance.structural_audit") as mock_audit,
            patch("wikify.core.store.db.get_engine"),
            patch("sqlmodel.Session", return_value=session_mock),
        ):
            mock_audit.return_value = report
            result = runner.invoke(app, ["wiki", "audit", "--fix"])

        assert result.exit_code == 0, result.output
        # Session.add and commit should be called for fix candidates
        assert session_mock.add.called or "Queued" in result.output

    def test_audit_no_fix_does_not_call_get_engine(self, tmp_path, monkeypatch):
        """wiki audit without --fix should not touch the DB engine."""
        _make_wiki_dir(tmp_path)
        monkeypatch.chdir(tmp_path)

        with (
            patch("wikify.wiki.maintenance.structural_audit") as mock_audit,
            patch("wikify.core.store.db.get_engine") as mock_ge,
        ):
            mock_audit.return_value = _make_structural_report()
            result = runner.invoke(app, ["wiki", "audit"])

        assert result.exit_code == 0, result.output
        mock_ge.assert_not_called()


# ── wiki sync routing tests ───────────────────────────────────────────────────


class TestWikiSyncRouting:
    def _run_sync_with_mocks(
        self,
        tmp_path,
        monkeypatch,
        *,
        contradict: bool,
        art_file_content: str = None,
    ):
        """Helper: run wiki sync with a single stale article."""
        wiki_dir = tmp_path / "data" / "wiki"
        concepts_dir = wiki_dir / "concepts"
        concepts_dir.mkdir(parents=True)
        (wiki_dir / "_index.md").write_text("# KB\n", encoding="utf-8")

        art_file = concepts_dir / "test_article.md"
        body = art_file_content or (
            "---\ntitle: Test\n---\n## Established\n\nOld claim. [REF:Smith 2021]\n"
        )
        art_file.write_text(body, encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        stale_art = _make_stale_article(
            file_path=str(art_file.relative_to(tmp_path / "data")),
            source_ids=["src_a", "src_b"],
        )

        from wikify.wiki.mapreduce import SourceExtraction

        mock_extraction = SourceExtraction(
            source_id="src_b",
            display_name="Jones 2023 - Paper",
            doc_type="paper",
            graph_role="standard",
            pagerank_score=0.0,
            extraction="YES: New contradicting finding.",
            is_relevant=True,
        )

        additive_mock = MagicMock(return_value="Additive updated body")
        revisionary_mock = MagicMock(return_value="Revisionary updated body")

        stale_exec = MagicMock()
        stale_exec.all.return_value = [stale_art]

        coverage_exec = MagicMock()
        coverage_exec.all.return_value = []

        update_exec = MagicMock()
        update_exec.first.return_value = stale_art

        session_mock = _make_session_ctx([stale_exec, coverage_exec, update_exec])

        with (
            patch("wikify.wiki.mapreduce.map_chunks_to_topic") as mock_map,
            patch("wikify.wiki.maintenance.detect_contradiction") as mock_dc,
            patch("wikify.wiki.maintenance.additive_update", additive_mock),
            patch("wikify.wiki.maintenance.revisionary_update", revisionary_mock),
            patch("wikify.wiki.persona.get_or_create_persona") as mock_persona,
            patch("wikify.wiki.builder.write_article"),
            patch("wikify.wiki.builder.generate_wiki_index"),
            patch("sqlmodel.Session", return_value=session_mock),
            patch("wikify.core.store.db.get_engine"),
        ):
            mock_dc.return_value = contradict
            mock_map.return_value = [mock_extraction]
            mock_persona.return_value = "Test persona"

            result = runner.invoke(app, ["wiki", "sync"])

        return result, additive_mock, revisionary_mock

    def test_sync_routes_to_additive_when_no_contradiction(self, tmp_path, monkeypatch):
        """When detect_contradiction returns False, additive_update should be called."""
        result, additive_mock, revisionary_mock = self._run_sync_with_mocks(
            tmp_path, monkeypatch, contradict=False
        )
        assert result.exit_code == 0, result.output
        assert additive_mock.called
        assert not revisionary_mock.called

    def test_sync_routes_to_revisionary_when_contradiction(self, tmp_path, monkeypatch):
        """When detect_contradiction returns True, revisionary_update should be called."""
        result, additive_mock, revisionary_mock = self._run_sync_with_mocks(
            tmp_path, monkeypatch, contradict=True
        )
        assert result.exit_code == 0, result.output
        assert revisionary_mock.called
        assert not additive_mock.called

    def test_sync_prints_summary_with_counts(self, tmp_path, monkeypatch):
        """wiki sync output should mention synced count."""
        result, _, _ = self._run_sync_with_mocks(tmp_path, monkeypatch, contradict=False)
        assert "Synced" in result.output

    def test_sync_skips_missing_files(self, tmp_path, monkeypatch):
        """wiki sync should gracefully skip articles whose files do not exist."""
        wiki_dir = tmp_path / "data" / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "_index.md").write_text("# KB\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        stale_art = _make_stale_article(
            file_path="wiki/concepts/nonexistent.md",
            source_ids=["src_a"],
        )

        stale_exec = MagicMock()
        stale_exec.all.return_value = [stale_art]
        session_mock = _make_session_ctx([stale_exec])

        with (
            patch("wikify.wiki.builder.generate_wiki_index"),
            patch("sqlmodel.Session", return_value=session_mock),
            patch("wikify.core.store.db.get_engine"),
        ):
            result = runner.invoke(app, ["wiki", "sync"])

        assert result.exit_code == 0, result.output
        assert "missing" in result.output or "Synced" in result.output


# ── wiki query escalation tests ───────────────────────────────────────────────


