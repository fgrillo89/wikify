"""Tests for v2 CLI IO logging — cli_invoked events into run/events.jsonl."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_cli(
    args: list[str],
    *,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src_path = str(Path.cwd() / "src")
    env["PYTHONPATH"] = (
        src_path if not env.get("PYTHONPATH") else src_path + os.pathsep + env["PYTHONPATH"]
    )
    return subprocess.run(
        [sys.executable, "-m", "wikify.cli", *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def _init_v2_bundle(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    init = _run_cli(
        ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    assert init.returncode == 0, init.stderr
    return bundle, corpus


def _read_events(bundle: Path) -> list[dict]:
    events_path = bundle / "run" / "events.jsonl"
    if not events_path.exists():
        return []
    text = events_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_v2_run_show_emits_cli_invoked(tmp_path: Path) -> None:
    """`wikify run show --run <bundle>` lands a cli_invoked event in events.jsonl."""
    bundle, _ = _init_v2_bundle(tmp_path)
    before = len(_read_events(bundle))

    result = _run_cli(["run", "show", "--run", str(bundle)])
    assert result.returncode == 0, result.stderr

    events = _read_events(bundle)
    assert len(events) > before
    cli_events = [e for e in events if e["type"] == "cli_invoked"]
    assert len(cli_events) >= 1
    e = cli_events[-1]
    assert e["actor"] == "cli"
    assert e["data"]["exit_code"] == 0
    assert "run show" in " ".join(e["data"]["argv"])
    assert "run_id:" in e["data"]["stdout_preview"]


def test_v2_cli_invoked_writes_io_files(tmp_path: Path) -> None:
    """Each cli_invoked event has stdout/stderr files under run/io/."""
    bundle, _ = _init_v2_bundle(tmp_path)
    result = _run_cli(["run", "show", "--run", str(bundle), "--format", "json"])
    assert result.returncode == 0, result.stderr

    events = _read_events(bundle)
    cli_events = [e for e in events if e["type"] == "cli_invoked"]
    e = cli_events[-1]
    stdout_path = Path(e["data"]["stdout_path"])
    assert stdout_path.is_file()
    assert stdout_path.read_text(encoding="utf-8") == result.stdout

    io_dir = bundle / "run" / "io"
    assert io_dir.is_dir()
    # Files are named <event_id>.{stdin,stdout,stderr}.txt
    assert any(stdout_path.parent == io_dir for _ in [None])


def test_v2_cli_invoked_carries_run_id(tmp_path: Path) -> None:
    """The Event envelope's run_id matches state.json after init."""
    bundle, _ = _init_v2_bundle(tmp_path)
    result = _run_cli(["run", "show", "--run", str(bundle), "--format", "json"])
    assert result.returncode == 0, result.stderr

    state = json.loads((bundle / "run" / "state.json").read_text(encoding="utf-8"))
    state_run_id = state["run_id"]

    events = _read_events(bundle)
    cli_events = [e for e in events if e["type"] == "cli_invoked"]
    assert all(e["run_id"] == state_run_id for e in cli_events)


def test_v2_cli_invoked_via_cwd(tmp_path: Path) -> None:
    """cwd resolution: command run from inside the bundle dir is logged into it."""
    bundle, _ = _init_v2_bundle(tmp_path)
    # Now invoke `run show` with cwd = bundle dir, no --run flag.
    result = _run_cli(["run", "show"], cwd=bundle)
    assert result.returncode == 0, result.stderr

    events = _read_events(bundle)
    cli_events = [e for e in events if e["type"] == "cli_invoked"]
    # The init call logged one cli_invoked? Actually no — at init time
    # state.json doesn't exist yet so v2 detection fails. So we expect
    # exactly 1: the run show invocation.
    assert len(cli_events) >= 1
    last = cli_events[-1]
    assert "run show" in " ".join(last["data"]["argv"])
    assert last["data"]["cwd"] == str(bundle)


def test_v2_takes_precedence_over_v1(tmp_path: Path) -> None:
    """When a dir has BOTH v2 and v1 markers, v2 wins (events.jsonl, not _meta/cli_io.jsonl)."""
    bundle, _ = _init_v2_bundle(tmp_path)
    # Plant a stale v1 marker.
    (bundle / "_session").mkdir()
    (bundle / "_session" / "session.json").write_text(
        json.dumps({"schema_version": 1, "bundle_root": str(bundle)}),
        encoding="utf-8",
    )

    result = _run_cli(["run", "show", "--run", str(bundle)])
    assert result.returncode == 0, result.stderr

    # Event landed in v2 path, not legacy.
    events = _read_events(bundle)
    cli_events = [e for e in events if e["type"] == "cli_invoked"]
    assert len(cli_events) >= 1
    legacy_path = bundle / "_meta" / "cli_io.jsonl"
    if legacy_path.exists():
        legacy = legacy_path.read_text(encoding="utf-8").strip().splitlines()
        # Legacy path must not have grown for the run show invocation.
        # (May contain whatever was there before, which is empty here.)
        assert all('"run", "show"' not in line for line in legacy)


