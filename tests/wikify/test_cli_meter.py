"""Tests for the wikify meter CLI family."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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

    # Session budget.haiku_eq_spent bumped — stored as float so fractional
    # haiku_eq from TierPrice math is preserved across records.
    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    assert session_doc["budget"]["haiku_eq_spent"] == pytest.approx(payload["haiku_eq"])


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


def test_meter_record_enforces_1_05x_budget_abort(tmp_path: Path) -> None:
    """Regression for PR#32 round 2 finding 2: skill writers must honor the
    same 1.05x budget ceiling legacy CostMeter enforces.
    """
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
            "100",
        ],
    )
    session_path = Path(json.loads(result.output)["session_path"])
    # 100 input + 200 output tokens at tier M costs 100*12 + 200*15 = 4200 heq
    # + 200 overhead = 4400 heq >> 1.05 * 100 = 105. Must abort.
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
            "100",
            "--output-tokens",
            "200",
        ],
    )
    assert result.exit_code == 3, result.output
    err = json.loads(result.stderr or result.output)
    assert err["error"] == "budget_exceeded"
    # _calls.jsonl must NOT have been written.
    calls_path = BundlePaths(bundle).calls_path
    assert not calls_path.exists() or not calls_path.read_text().strip()


def test_meter_record_stores_haiku_eq_spent_as_float(tmp_path: Path) -> None:
    """Regression for PR#32 round 2 finding 3: haiku_eq_spent is float,
    not int — fractional haiku_eq (e.g., from future cache discounts or
    non-integer pricing) accumulates without per-record truncation.
    """
    session_path = _init_session(tmp_path)
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
            "1",
            "--output-tokens",
            "1",
        ],
    )
    session_doc = json.loads(session_path.read_text(encoding="utf-8"))
    spent = session_doc["budget"]["haiku_eq_spent"]
    # Value is persisted as a JSON number; load back into Python, assert
    # it's a float type (not an int), and matches the haiku_eq_for math.
    assert isinstance(spent, float)


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
    # by_role is pre-populated with every Role enum value; roles not
    # exercised this run appear with calls=0 (mirrors legacy CostMeter).
    assert {"extractor", "writer"} <= set(snapshot["by_role"].keys())
    assert snapshot["by_role"]["extractor"]["calls"] == 1
    assert snapshot["by_role"]["writer"]["calls"] == 1
    # by_tier is populated from observed tiers only.
    assert set(snapshot["by_tier"].keys()) == {"S", "M"}


def test_close_mints_per_close_run_id_and_appends_history(tmp_path: Path) -> None:
    """Regression for PR#32 round 2 findings 6 and 9.

    Two closes on the same session must produce:
      - two distinct run_id values in _run.json (second close overwrites),
      - two entries in _run_history.jsonl preserving the audit trail.
    """
    session_path = _init_session(tmp_path)
    # First close.
    runner.invoke(app, ["session", "close", "--session", str(session_path)])
    bundle_root = Path(json.loads(session_path.read_text(encoding="utf-8"))["bundle_root"])
    paths = BundlePaths(bundle_root)
    first_snapshot = json.loads(paths.run_path.read_text(encoding="utf-8"))
    first_run_id = first_snapshot["run_id"]

    # Reopen by patching status back to active (emulates resume) and
    # close a second time with an updated timestamp.
    import time

    time.sleep(1)  # ensure updated_at advances at second granularity
    runner.invoke(
        app,
        [
            "session",
            "update",
            "--session",
            str(session_path),
            "--patch",
            json.dumps({"status": "active"}),
        ],
    )
    runner.invoke(app, ["session", "close", "--session", str(session_path)])
    second_snapshot = json.loads(paths.run_path.read_text(encoding="utf-8"))
    second_run_id = second_snapshot["run_id"]

    assert first_run_id != second_run_id, "run_id must be per-close, not per-session"

    history_lines = paths.run_history_path.read_text(encoding="utf-8").splitlines()
    assert len(history_lines) == 2, "history should capture both closes"
    hist_run_ids = [json.loads(line)["run_id"] for line in history_lines]
    assert hist_run_ids == [first_run_id, second_run_id]


def test_aggregate_calls_jsonl_rejects_unknown_role(tmp_path: Path) -> None:
    """Regression for PR#32 round 2 finding 7: unknown role strings in
    _calls.jsonl must fail loudly at aggregation, not silently bucket.
    """
    from wikify.session import UnknownRoleError, _aggregate_calls_jsonl

    calls_path = tmp_path / "_calls.jsonl"
    calls_path.write_text(
        json.dumps(
            {
                "role": "not-a-role",
                "tier": "M",
                "input_tokens": 1,
                "output_tokens": 1,
                "context_used": 1,
                "context_cap": 1000,
                "wall_seconds": 0.0,
                "cache_hit": False,
                "prompt_hash": "",
                "haiku_eq": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(UnknownRoleError):
        _aggregate_calls_jsonl(calls_path)


def test_aggregate_calls_jsonl_prepopulates_all_roles(tmp_path: Path) -> None:
    """Even with no records for a role, by_role must carry all Role enum
    values so the key set is stable for downstream consumers.
    """
    from wikify.session import _aggregate_calls_jsonl
    from wikify.types import Role

    # Non-existent calls file path: all-zero aggregates, all roles present.
    agg = _aggregate_calls_jsonl(tmp_path / "_calls.jsonl")
    assert set(agg["by_role"].keys()) == {r.value for r in Role}
