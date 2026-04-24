"""Unit tests for the durable session module and `wikify session` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wikify.cli import app
from wikify.paths import BundlePaths
from wikify.session import (
    SCHEMA_VERSION,
    SchemaVersionMismatchError,
    SessionV1,
    apply_merge_patch,
    init_session,
    load_session,
    save_session,
)

runner = CliRunner()


def _write_session(bundle: Path, corpus: Path) -> Path:
    session = init_session(bundle_root=bundle, corpus_root=corpus)
    paths = BundlePaths(bundle)
    save_session(paths.session_path, session)
    return paths.session_path


def test_init_creates_v1_session(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    session_path = _write_session(bundle, corpus)

    data = json.loads(session_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["strategy"] == "baseline"
    assert data["status"] == "active"
    assert set(data["stages"].keys()) == {"seed_selection", "extract", "write"}
    assert data["pages"] == []
    assert data["telemetry_paths"]["run_path"].endswith("_run.json")
    assert data["telemetry_paths"]["calls_path"].endswith("_calls.jsonl")


def test_load_session_rejects_mismatched_schema_version(tmp_path: Path) -> None:
    session_path = _write_session(tmp_path / "bundle", tmp_path / "corpus")
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    session_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SchemaVersionMismatchError):
        load_session(session_path)


def test_apply_merge_patch_updates_and_deletes() -> None:
    base = SessionV1(
        session_id="abc",
        strategy="baseline",
        bundle_root="/bundle",
        corpus_root="/corpus",
        created_at="2026-04-23T00:00:00Z",
        updated_at="2026-04-23T00:00:00Z",
        telemetry_paths={"run_path": "/r", "calls_path": "/c"},
    )
    patched = apply_merge_patch(
        base,
        {
            "pages": [
                {"page_id": "Atomic Layer Deposition", "status": "planned"},
            ],
            "budget": {"haiku_eq_target": 500_000},
        },
    )
    assert len(patched.pages) == 1
    assert patched.pages[0].page_id == "Atomic Layer Deposition"
    assert patched.budget.haiku_eq_target == 500_000
    assert patched.budget.haiku_eq_spent == 0  # untouched


def test_cli_init_show_update_checkpoint_close(tmp_path: Path) -> None:
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
            "1000",
        ],
    )
    assert result.exit_code == 0, result.output
    init_out = json.loads(result.output)
    session_path = Path(init_out["session_path"])
    assert session_path.exists()
    assert init_out["schema_version"] == 1

    result = runner.invoke(app, ["session", "show", "--session", str(session_path)])
    assert result.exit_code == 0, result.output
    show_out = json.loads(result.output)
    assert show_out["strategy"] == "baseline"
    assert show_out["status"] == "active"
    assert show_out["budget"]["haiku_eq_target"] == 1000
    assert show_out["page_counts"]["total"] == 0

    patch = {
        "pages": [{"page_id": "ALD", "status": "planned"}],
        "stages": {"seed_selection": {"status": "done"}},
    }
    result = runner.invoke(
        app,
        ["session", "update", "--session", str(session_path), "--patch", json.dumps(patch)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True

    result = runner.invoke(
        app,
        ["session", "checkpoint", "--session", str(session_path), "--label", "after-seeds"],
    )
    assert result.exit_code == 0, result.output
    checkpoint_path = Path(json.loads(result.output)["checkpoint_path"])
    assert checkpoint_path.exists()
    snapshot = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert snapshot["pages"][0]["page_id"] == "ALD"
    assert snapshot["stages"]["seed_selection"]["status"] == "done"

    result = runner.invoke(app, ["session", "close", "--session", str(session_path)])
    assert result.exit_code == 0, result.output
    close_payload = json.loads(result.output)
    final = json.loads(session_path.read_text(encoding="utf-8"))
    assert final["status"] == "closed"
    run_path = Path(close_payload["run_path"])
    assert run_path.exists()
    snapshot = json.loads(run_path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert snapshot["strategy"] == "baseline"
    assert snapshot["status"] == "closed"
    assert snapshot["session_id"] == final["session_id"]
    assert set(snapshot["stages"].keys()) == {"seed_selection", "extract", "write"}
    assert "page_counts" in snapshot
    assert "telemetry_paths" in snapshot


def test_cli_init_refuses_to_overwrite(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    runner.invoke(
        app, ["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    result = runner.invoke(
        app, ["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    assert result.exit_code != 0


def test_cli_lock_blocks_update_and_close(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    init_result = runner.invoke(
        app, ["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    session_path = Path(json.loads(init_result.output)["session_path"])

    # Outside owner grabs the lock.
    lock_result = runner.invoke(
        app, ["session", "lock", "--session", str(session_path), "--owner", "outsider"]
    )
    assert lock_result.exit_code == 0, lock_result.output

    # Update must now fail with exit 2 and a structured lock_held payload.
    upd = runner.invoke(
        app,
        ["session", "update", "--session", str(session_path), "--patch", "{}"],
    )
    assert upd.exit_code == 2, upd.output + upd.stderr
    err_payload = json.loads(upd.stderr or upd.output)
    assert err_payload["error"] == "lock_held"
    assert err_payload["owner"] == "outsider"

    # Close is gated by the same lock.
    clos = runner.invoke(app, ["session", "close", "--session", str(session_path)])
    assert clos.exit_code == 2

    # After unlock, update and close proceed.
    unlock = runner.invoke(app, ["session", "unlock", "--session", str(session_path)])
    assert unlock.exit_code == 0
    upd2 = runner.invoke(
        app, ["session", "update", "--session", str(session_path), "--patch", "{}"]
    )
    assert upd2.exit_code == 0
    clos2 = runner.invoke(app, ["session", "close", "--session", str(session_path)])
    assert clos2.exit_code == 0


def test_stale_lock_past_ttl_is_reclaimed(tmp_path: Path) -> None:
    from wikify.paths import BundlePaths
    from wikify.session import acquire_lock

    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    init_result = runner.invoke(
        app, ["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    session_path = Path(json.loads(init_result.output)["session_path"])

    # Write a stale lock record directly: expired 1 hour ago.
    import json as _json

    paths = BundlePaths(bundle)
    paths.session_lock_path.write_text(
        _json.dumps(
            {
                "owner": "ghost",
                "pid": 99999,
                "acquired_at": "2000-01-01T00:00:00Z",
                "expires_at": "2000-01-01T01:00:00Z",
                "ttl_seconds": 3600,
            }
        ),
        encoding="utf-8",
    )

    # New acquire should succeed by reclaiming the stale lock.
    acquire_lock(session_path, owner="new-owner")
    record = _json.loads(paths.session_lock_path.read_text(encoding="utf-8"))
    assert record["owner"] == "new-owner"


def test_bundle_paths_exposes_session_layout(tmp_path: Path) -> None:
    paths = BundlePaths(tmp_path / "bundle")
    assert paths.session_dir == tmp_path / "bundle" / "_session"
    assert paths.session_path == paths.session_dir / "session.json"
    assert paths.session_checkpoints_dir == paths.session_dir / "checkpoints"
    assert paths.session_lock_path == paths.session_dir / "session.lock"
    assert paths.scratch_dir == tmp_path / "bundle" / "_scratch"
