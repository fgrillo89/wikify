"""Tests for `wikify eval` — corpus-free metrics over a v2 bundle."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_wiki_commit import _setup_validated  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def _commit_one_article(tmp_path: Path):
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    return bundle, slug


def test_eval_writes_default_report(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(app, ["eval", "--run", str(bundle.root)])
    assert result.exit_code == 0, result.output
    report_path = bundle.derived_dir / "eval.json"
    assert report_path.is_file()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["n_articles"] == 1
    assert "g_evidence" in data and "g_links" in data


def test_eval_custom_report_path(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    custom = tmp_path / "metrics" / "out.json"
    result = runner.invoke(
        app,
        ["eval", "--run", str(bundle.root), "--report", str(custom)],
    )
    assert result.exit_code == 0, result.output
    assert custom.is_file()
    data = json.loads(custom.read_text(encoding="utf-8"))
    assert data["n_articles"] == 1


def test_eval_json_envelope(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(
        app, ["eval", "--run", str(bundle.root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["n_articles"] == 1
    assert data["report"].endswith("eval.json")


def test_eval_on_empty_bundle_returns_zero_articles(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    result = runner.invoke(app, ["eval", "--run", str(bundle), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["n_articles"] == 0
    assert data["n_people"] == 0
