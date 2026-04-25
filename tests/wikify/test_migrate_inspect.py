"""Tests for `wikify migrate inspect`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def _make_legacy_bundle(root: Path) -> Path:
    """Build a minimal v1 bundle on disk so the inspector has something to count."""
    (root / "_session").mkdir(parents=True)
    (root / "_session" / "session.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (root / "_session" / "checkpoints").mkdir()
    (root / "_calls.jsonl").write_text(
        '{"role": "extractor", "tier": "S"}\n', encoding="utf-8"
    )
    (root / "_run.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (root / "articles").mkdir()
    (root / "articles" / "foo.md").write_text("# Foo\n", encoding="utf-8")
    (root / "articles" / "bar.md").write_text("# Bar\n", encoding="utf-8")
    (root / "people").mkdir()
    (root / "_meta").mkdir()
    return root


# --- text format -------------------------------------------------------


def test_migrate_inspect_reports_v1_text(tmp_path: Path) -> None:
    bundle = _make_legacy_bundle(tmp_path / "bundle")
    result = runner.invoke(app, ["migrate", "inspect", str(bundle)])
    assert result.exit_code == 0
    assert "layout:  v1" in result.stdout
    assert "_session/" in result.stdout
    assert "_calls.jsonl" in result.stdout
    assert "_run.json" in result.stdout
    assert "articles/" in result.stdout
    assert "v2 artifacts:    none" in result.stdout


def test_migrate_inspect_reports_v2_text(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "run").mkdir(parents=True)
    (bundle / "run" / "state.json").write_text("{}", encoding="utf-8")
    (bundle / "wiki").mkdir()
    result = runner.invoke(app, ["migrate", "inspect", str(bundle)])
    assert result.exit_code == 0
    assert "layout:  v2" in result.stdout
    assert "run/" in result.stdout
    assert "run/state.json" in result.stdout
    assert "wiki/" in result.stdout
    assert "legacy artifacts: none" in result.stdout


def test_migrate_inspect_reports_unknown_text(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    result = runner.invoke(app, ["migrate", "inspect", str(bundle)])
    assert result.exit_code == 0
    assert "layout:  unknown" in result.stdout
    assert "legacy artifacts: none" in result.stdout
    assert "v2 artifacts:    none" in result.stdout


def test_migrate_inspect_errors_on_missing_dir(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["migrate", "inspect", str(tmp_path / "does-not-exist")]
    )
    assert result.exit_code == 1
    assert "not a directory" in result.stderr


# --- json format -------------------------------------------------------


def test_migrate_inspect_v1_json_envelope(tmp_path: Path) -> None:
    bundle = _make_legacy_bundle(tmp_path / "bundle")
    result = runner.invoke(
        app, ["migrate", "inspect", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["layout"] == "v1"
    assert data["bundle"] == str(bundle)
    legacy = data["legacy_artifacts"]
    assert "_session/" in legacy
    assert legacy["_session/"]["kind"] == "dir"
    assert legacy["_calls.jsonl"]["kind"] == "file"
    assert legacy["_calls.jsonl"]["size_bytes"] > 0
    assert legacy["articles/"]["file_count"] == 2
    assert data["v2_artifacts"] == {}


def test_migrate_inspect_bad_format(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    result = runner.invoke(
        app, ["migrate", "inspect", str(bundle), "--format", "yaml"]
    )
    assert result.exit_code != 0
