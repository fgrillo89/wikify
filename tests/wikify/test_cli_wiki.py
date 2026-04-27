"""Tests for `wikify wiki ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_wiki_commit import _setup_validated  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def test_wiki_list_empty(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    result = runner.invoke(app, ["wiki", "list", "--run", str(bundle)])
    assert result.exit_code == 0


def test_wiki_commit_then_list(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    result = runner.invoke(
        app,
        ["wiki", "commit", slug, "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slug"] == slug
    listing = runner.invoke(
        app, ["wiki", "list", "--run", str(bundle.root), "--format", "json"]
    )
    assert listing.exit_code == 0
    items = json.loads(listing.output)["items"]
    assert any(it["slug"] == slug for it in items)


def test_wiki_commit_rejects_unvalidated(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app, ["wiki", "commit", "ald", "--run", str(bundle)]
    )
    assert result.exit_code != 0


def test_wiki_show_after_commit(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(
        app, ["wiki", "commit", slug, "--run", str(bundle.root)]
    )
    result = runner.invoke(
        app,
        ["wiki", "show", slug, "--run", str(bundle.root), "--full"],
    )
    assert result.exit_code == 0
    assert "Atomic Layer Deposition" in result.output


def test_wiki_find_text(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        ["wiki", "find", "atomic layer", "--run", str(bundle.root), "--text"],
    )
    assert result.exit_code == 0
    assert slug in result.output


def test_wiki_build_indexes(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app,
        ["wiki", "build", "indexes", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "path" in data


def test_wiki_check(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    result = runner.invoke(
        app, ["wiki", "check", "--run", str(bundle.root), "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["articles"] == 1


def test_wiki_commit_lock_held_returns_exit_2(tmp_path: Path) -> None:
    """When the bundle lock is held by someone else, `wiki commit` exits 2."""
    from wikify.bundle.run.lock import acquire_lock

    bundle, slug = _setup_validated(tmp_path)
    # Pre-acquire the lock as another owner so commit_page contends.
    acquire_lock(bundle, owner="other-process", ttl_seconds=120)
    result = runner.invoke(
        app,
        ["wiki", "commit", slug, "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "lock_held"
    assert payload["owner"] == "other-process"
