"""Tests for `wikify run ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def _bundle_dir(tmp_path: Path) -> Path:
    return tmp_path / "bundle"


def test_run_init_creates_bundle_layout(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    result = runner.invoke(
        app,
        [
            "run", "init",
            "--bundle", str(bundle),
            "--corpus", str(corpus),
            "--strategy", "baseline",
            "--target-haiku-eq", "1000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "run_id:" in result.output
    assert (bundle / "run" / "state.json").is_file()


def test_run_init_json_envelope(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    result = runner.invoke(
        app,
        [
            "run", "init",
            "--bundle", str(bundle),
            "--corpus", str(corpus),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["run_id"].startswith("run-")
    assert data["state_path"].endswith("state.json")


def test_run_init_rejects_already_initialised_bundle(tmp_path: Path) -> None:
    """A second run init against the same path must refuse, to avoid
    overwriting an existing run/state.json silently.
    """
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    first = runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    assert first.exit_code == 0
    second = runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    assert second.exit_code != 0


def test_run_show_after_init(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(app, ["run", "show", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "run_id:" in result.output
    assert "status:" in result.output


def test_run_close_changes_status(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    show = runner.invoke(
        app, ["run", "show", "--run", str(bundle), "--format", "json"]
    )
    assert json.loads(show.output)["status"] == "completed"


def test_run_record_call_appends_cost_event(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    result = runner.invoke(
        app,
        [
            "run", "record-call",
            "--run", str(bundle),
            "--role", "writer",
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
            "--tokens-in", "100",
            "--tokens-out", "25",
            "--stage", "write",
            "--concept-id", "memristor",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    events = json.loads(
        runner.invoke(
            app,
            [
                "run", "list", "events",
                "--run", str(bundle),
                "--type", "call",
                "--format", "json",
            ],
        ).output
    )
    assert events[-1]["concept_id"] == "memristor"
    assert events[-1]["data"]["input_tokens"] == 100


def test_run_lock_then_unlock(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    lock = runner.invoke(
        app, ["run", "lock", "--run", str(bundle), "--owner", "a"]
    )
    assert lock.exit_code == 0
    assert "locked by a" in lock.output

    # Second lock by a different owner returns exit 2.
    lock2 = runner.invoke(
        app, ["run", "lock", "--run", str(bundle), "--owner", "b"]
    )
    assert lock2.exit_code == 2

    unlock = runner.invoke(app, ["run", "unlock", "--run", str(bundle)])
    assert unlock.exit_code == 0


def test_run_list_events_tail(tmp_path: Path) -> None:
    bundle = _bundle_dir(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)],
    )
    runner.invoke(
        app,
        ["run", "close", "--run", str(bundle), "--status", "completed"],
    )
    result = runner.invoke(
        app, ["run", "list", "events", "--run", str(bundle), "--tail", "5"]
    )
    assert result.exit_code == 0
    assert "stage_changed" in result.output
    assert "run_closed" in result.output


def test_run_show_no_bundle_context(tmp_path: Path, monkeypatch) -> None:
    """When no bundle is resolved, exit with a clear error envelope."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run", "show"])
    assert result.exit_code == 1
