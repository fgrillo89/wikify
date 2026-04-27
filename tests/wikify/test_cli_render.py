"""Tests for `wikify render` — static-site renderer over a bundle."""

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


def test_render_writes_html_site(tmp_path: Path) -> None:
    bundle, slug = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--format",
            "html",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert (out / "articles").is_dir()
    # The rendered article file should mention the page title.
    html_files = list((out / "articles").glob("*.html"))
    assert html_files, "expected at least one rendered article HTML"
    text = html_files[0].read_text(encoding="utf-8")
    assert "Atomic Layer Deposition" in text


def test_render_default_out_under_derived(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(app, ["render", "--bundle", str(bundle.root)])
    assert result.exit_code == 0, result.output
    assert (bundle.derived_dir / "site" / "index.html").is_file()


def test_render_rejects_unsupported_format(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--format", "pdf"]
    )
    assert result.exit_code != 0


def test_render_json_envelope(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--out",
            str(out),
            "--output-format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["pages"] >= 1
    assert payload["out"].endswith("site")
