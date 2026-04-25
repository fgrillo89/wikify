from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_cli(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
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
        check=False,
    )


def test_cli_io_log_captures_session_init_output(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"

    result = _run_cli(["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)])

    assert result.returncode == 0, result.stderr
    events_path = bundle / "_meta" / "cli_io.jsonl"
    assert events_path.exists()

    event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["type"] == "cli_invoked"
    assert event["exit_code"] == 0
    assert "session init" in " ".join(event["argv"])
    assert "session_path" in event["stdout_preview"]
    assert Path(event["stdout_path"]).read_text(encoding="utf-8") == result.stdout
    assert Path(event["stderr_path"]).read_text(encoding="utf-8") == result.stderr


def test_cli_io_log_captures_stdin_for_session_update(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    init = _run_cli(["session", "init", "--bundle", str(bundle), "--corpus", str(corpus)])
    assert init.returncode == 0, init.stderr
    session_path = Path(json.loads(init.stdout)["session_path"])

    patch = '{"iteration": "refine"}'
    result = _run_cli(
        ["session", "update", "--session", str(session_path), "--patch", "-"],
        stdin=patch,
    )

    assert result.returncode == 0, result.stderr
    events = [
        json.loads(line)
        for line in (bundle / "_meta" / "cli_io.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event = events[-1]
    assert event["exit_code"] == 0
    assert event["stdin_preview"] == patch
    assert Path(event["stdin_path"]).read_text(encoding="utf-8") == patch
