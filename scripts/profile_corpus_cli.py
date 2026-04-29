"""Profile a single ``wikify corpus ...`` invocation.

Captures wall-clock, peak RSS (parent + child), bytes on stdout/stderr,
and exit code. Designed for repeated calls during the corpus-CLI audit;
no fancy formatting, just a JSONL-friendly line per run.

Usage::

    uv run python scripts/profile_corpus_cli.py [-n N] -- wikify corpus find "ALD" --top-k 5
    # or shorter:
    uv run python scripts/profile_corpus_cli.py -- wikify corpus schema

Each run prints a single JSON object with keys:
    cmd, duration_s, exit, stdout_bytes, stderr_bytes, peak_rss_kb,
    stdout_lines, stderr_lines.

If ``-n N`` is passed, runs the same command ``N`` times and emits one
line per run. The first call typically pays cold cache; subsequent ones
are warm.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

try:
    import resource  # POSIX only.
except ModuleNotFoundError:  # Windows
    resource = None  # type: ignore[assignment]


def _peak_rss_kb_after_child() -> int:
    """Best-effort peak RSS for the child process.

    On POSIX we read ``ru_maxrss`` of children (kilobytes on Linux,
    bytes on macOS — we do not normalise across OSes; the script is
    primarily run on the dev box). On Windows ``resource`` is missing
    entirely, so we return 0 and rely on duration + bytes.
    """
    if resource is None:
        return 0
    rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return int(rusage.ru_maxrss)


def run_once(cmd: list[str]) -> dict[str, Any]:
    start = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 — caller-supplied args, intentional
        cmd,
        capture_output=True,
        check=False,
    )
    duration = time.perf_counter() - start
    return {
        "cmd": " ".join(cmd),
        "duration_s": round(duration, 4),
        "exit": proc.returncode,
        "stdout_bytes": len(proc.stdout),
        "stderr_bytes": len(proc.stderr),
        "stdout_lines": proc.stdout.count(b"\n"),
        "stderr_lines": proc.stderr.count(b"\n"),
        "peak_rss_kb": _peak_rss_kb_after_child(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Profile a wikify corpus CLI invocation."
    )
    ap.add_argument("-n", type=int, default=1, help="Repeat count.")
    ap.add_argument(
        "--show-stdout",
        action="store_true",
        help="Tee child stdout/stderr to this process's stderr after each run.",
    )
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to profile.")
    args = ap.parse_args()
    if not args.cmd:
        ap.error("supply a command after --, e.g. -- wikify corpus schema")
    cmd = args.cmd[1:] if args.cmd[0] == "--" else args.cmd

    for _ in range(args.n):
        rec = run_once(cmd)
        print(json.dumps(rec))
        if args.show_stdout:
            print("-- last invocation stdout/stderr --", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
