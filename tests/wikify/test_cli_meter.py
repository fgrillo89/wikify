"""Tests for the wikify meter CLI family."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app
from wikify.paths import BundlePaths

runner = CliRunner()


def _init_session(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    result = runner.invoke(
        app,
        [
            "session",
            "init",
            "--bundle",
            str(bundle),
            "--corpus",
            str(corpus),
            "--strategy",
            "baseline",
            "--budget-target",
            "1000000",
        ],
    )
    assert result.exit_code == 0, result.output
    return Path(json.loads(result.output)["session_path"])


def test_meter_record_appends_calls_jsonl_and_updates_budget(tmp_path: Path) -> None:
    session_path = _init_session(tmp_path)

    result = runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "writer",
            "--tier",
            "M",
            "--input-tokens",
            "1000",
            "--output-tokens",
            "500",
            "--wall-seconds",
            "2.5",
            "--prompt-hash",
            "abcd1234",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["role"] == "writer"
    assert payload["tier"] == "M"
    assert payload["haiku_eq"] > 0

    # Calls file exists with the record.
    bundle_root = Path(
        json.loads(session_path.read_text(encoding="utf-8"))["bundle_root"]
    )
    calls_path = BundlePaths(bundle_root).calls_path
    lines = calls_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["role"] == "writer"
    assert record["tier"] == "M"
    assert record["input_tokens"] == 1000
    assert record["output_tokens"] == 500
    assert record["context_used"] == 1000
    assert record["wall_seconds"] == 2.5
    assert record["prompt_hash"] == "abcd1234"

    # Session budget.haiku_eq_spent bumped.
    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    assert session_doc["budget"]["haiku_eq_spent"] == int(payload["haiku_eq"])


def test_meter_record_rejects_input_tokens_above_context_cap(tmp_path: Path) -> None:
    session_path = _init_session(tmp_path)
    result = runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "writer",
            "--tier",
            "M",
            "--input-tokens",
            "1000",
            "--output-tokens",
            "500",
            "--context-cap",
            "100",
        ],
    )
    assert result.exit_code != 0


def test_meter_record_rejects_invalid_role_or_tier(tmp_path: Path) -> None:
    session_path = _init_session(tmp_path)
    bad_role = runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "not-a-role",
            "--tier",
            "M",
            "--input-tokens",
            "1",
            "--output-tokens",
            "1",
        ],
    )
    assert bad_role.exit_code != 0
    bad_tier = runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "writer",
            "--tier",
            "XL",
            "--input-tokens",
            "1",
            "--output-tokens",
            "1",
        ],
    )
    assert bad_tier.exit_code != 0


def test_meter_record_accumulates_in_session_close_snapshot(tmp_path: Path) -> None:
    session_path = _init_session(tmp_path)
    # Two records, different roles/tiers.
    runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "extractor",
            "--tier",
            "S",
            "--input-tokens",
            "200",
            "--output-tokens",
            "80",
        ],
    )
    runner.invoke(
        app,
        [
            "meter",
            "record",
            "--session",
            str(session_path),
            "--role",
            "writer",
            "--tier",
            "M",
            "--input-tokens",
            "1500",
            "--output-tokens",
            "600",
            "--cache-hit",
        ],
    )

    close = runner.invoke(app, ["session", "close", "--session", str(session_path)])
    assert close.exit_code == 0
    run_path = Path(json.loads(close.output)["run_path"])
    snapshot = json.loads(run_path.read_text(encoding="utf-8"))
    assert snapshot["calls"] == 2
    assert snapshot["cache_hit_rate"] == 0.5
    assert snapshot["budget_used_haiku_eq"] > 0
    # Legacy-shape context sub-dict
    assert snapshot["context"]["used_max"] == 1500
    # Per-role / per-tier breakdowns
    assert set(snapshot["by_role"].keys()) == {"extractor", "writer"}
    assert set(snapshot["by_tier"].keys()) == {"S", "M"}
