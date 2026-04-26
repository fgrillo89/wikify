"""Process-boundary CLI input/output telemetry for v2 bundles.

The skill path interacts with Wikify through CLI stdout/stderr.
Capturing that boundary lets replay tools reconstruct what the model
actually saw without asking agents to hand-maintain logs.

When a command runs in (or against) a v2 bundle, a structured
``cli_invoked`` :class:`Event` lands in ``run/events.jsonl`` and
large stdout/stderr/stdin spill to
``run/io/<event_id>.{stdin,stdout,stderr}.txt``.

Disable with ``WIKIFY_CLI_IO_LOG=0``.
"""

from __future__ import annotations

import io
import os
import sys
import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from typer import Typer

from ..api import Bundle, LayoutMismatchError
from ..bundle.run.events import Event, append_event
from ..bundle.run.state import load_state

_DISABLE_ENV = "WIKIFY_CLI_IO_LOG"
_SENSITIVE_FLAG_PARTS = ("key", "token", "secret", "password", "credential")
_PREVIEW_CHARS = 500


class _TeeWriter(io.TextIOBase):
    def __init__(self, primary: TextIO, capture: TextIO) -> None:
        self._primary = primary
        self._capture = capture

    @property
    def encoding(self) -> str | None:
        return getattr(self._primary, "encoding", None)

    @property
    def errors(self) -> str | None:
        return getattr(self._primary, "errors", None)

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return self._primary.isatty()

    def write(self, text: str) -> int:
        written = self._primary.write(text)
        self._capture.write(text)
        return written

    def flush(self) -> None:
        self._primary.flush()
        self._capture.flush()


class _TeeReader(io.TextIOBase):
    def __init__(self, primary: TextIO, capture: TextIO) -> None:
        self._primary = primary
        self._capture = capture

    @property
    def encoding(self) -> str | None:
        return getattr(self._primary, "encoding", None)

    @property
    def errors(self) -> str | None:
        return getattr(self._primary, "errors", None)

    def readable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return self._primary.isatty()

    def read(self, size: int = -1) -> str:
        text = self._primary.read(size)
        self._capture.write(text)
        self._capture.flush()
        return text

    def readline(self, size: int = -1) -> str:
        text = self._primary.readline(size)
        self._capture.write(text)
        self._capture.flush()
        return text

    def readlines(self, hint: int = -1) -> list[str]:
        lines = self._primary.readlines(hint)
        self._capture.writelines(lines)
        self._capture.flush()
        return lines


class _V2InvocationLog:
    """CLI-IO log writer for v2 bundles. Emits a ``cli_invoked`` Event."""

    def __init__(self, *, argv: Sequence[str], cwd: Path, bundle: Bundle) -> None:
        self.event_id = uuid4().hex
        self.argv = list(argv)
        self.cwd = cwd
        self.bundle = bundle
        self.started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.started = time.perf_counter()

        self.io_dir = bundle.io_dir
        self.io_dir.mkdir(parents=True, exist_ok=True)
        self.stdin_path = self.io_dir / f"{self.event_id}.stdin.txt"
        self.stdout_path = self.io_dir / f"{self.event_id}.stdout.txt"
        self.stderr_path = self.io_dir / f"{self.event_id}.stderr.txt"

    @contextmanager
    def capture(self):
        with (
            self.stdin_path.open("w", encoding="utf-8") as stdin_capture,
            self.stdout_path.open("w", encoding="utf-8") as stdout_capture,
            self.stderr_path.open("w", encoding="utf-8") as stderr_capture,
        ):
            old_stdin = sys.stdin
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdin = _TeeReader(old_stdin, stdin_capture)
            sys.stdout = _TeeWriter(old_stdout, stdout_capture)
            sys.stderr = _TeeWriter(old_stderr, stderr_capture)
            try:
                yield
            finally:
                sys.stdin = old_stdin
                sys.stdout = old_stdout
                sys.stderr = old_stderr

    def write_event(self, *, exit_code: int) -> None:
        duration_ms = int((time.perf_counter() - self.started) * 1000)
        try:
            state = load_state(self.bundle)
            run_id = state.run_id
        except Exception:
            run_id = ""
        event = Event(
            event_id=self.event_id,
            run_id=run_id,
            type="cli_invoked",
            at=self.started_at,
            actor="cli",
            stage=None,
            data={
                "argv": _redact_argv(self.argv),
                "cwd": str(self.cwd),
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdin_path": str(self.stdin_path),
                "stdout_path": str(self.stdout_path),
                "stderr_path": str(self.stderr_path),
                "stdin_preview": _preview(self.stdin_path),
                "stdout_preview": _preview(self.stdout_path),
                "stderr_preview": _preview(self.stderr_path),
            },
        )
        append_event(self.bundle, event)


def run_with_io_logging(app: Typer, argv: Sequence[str] | None = None) -> None:
    """Run a Typer app while teeing stdin/stdout/stderr into bundle telemetry.

    Logging is enabled when a v2 bundle can be inferred from
    ``--run``, ``--bundle``, or the current working directory. Set
    ``WIKIFY_CLI_IO_LOG=0`` to disable.
    """
    effective_argv = list(argv or sys.argv)
    log = _build_invocation_log(effective_argv)
    if log is None:
        app()
        return

    exit_code = 0
    try:
        with log.capture():
            app()
    except SystemExit as exc:
        exit_code = _system_exit_code(exc)
        log.write_event(exit_code=exit_code)
        raise
    except BaseException:
        exit_code = 1
        log.write_event(exit_code=exit_code)
        raise
    else:
        log.write_event(exit_code=exit_code)


def _build_invocation_log(argv: Sequence[str]) -> _V2InvocationLog | None:
    if os.environ.get(_DISABLE_ENV) == "0":
        return None
    cwd = Path.cwd()
    v2_root = _resolve_v2_bundle_root(argv[1:], cwd)
    if v2_root is None:
        return None
    try:
        bundle = Bundle.open(v2_root)
    except (LayoutMismatchError, FileNotFoundError):
        return None
    return _V2InvocationLog(argv=argv, cwd=cwd, bundle=bundle)


def _resolve_v2_bundle_root(args: Sequence[str], cwd: Path) -> Path | None:
    """Return a v2 bundle root if --run, --bundle, or cwd resolves to one."""
    run_path = _option_path(args, "--run")
    if run_path is not None and (run_path / "run" / "state.json").is_file():
        return run_path.resolve()

    bundle_path = _option_path(args, "--bundle")
    if bundle_path is not None and (bundle_path / "run" / "state.json").is_file():
        return bundle_path.resolve()

    if (cwd / "run" / "state.json").is_file():
        return cwd.resolve()
    return None


def _option_path(args: Sequence[str], name: str) -> Path | None:
    prefix = f"{name}="
    for idx, arg in enumerate(args):
        if arg == name and idx + 1 < len(args):
            return Path(args[idx + 1])
        if arg.startswith(prefix):
            return Path(arg[len(prefix):])
    return None


def _system_exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    return 1


def _preview(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) <= _PREVIEW_CHARS:
        return text
    return text[:_PREVIEW_CHARS] + "..."


def _redact_argv(argv: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        if "=" in arg:
            name, value = arg.split("=", 1)
            if _is_sensitive_flag(name):
                redacted.append(f"{name}=<redacted>")
            else:
                redacted.append(f"{name}={value}")
            continue
        redacted.append("<redacted>" if _is_sensitive_flag(arg) else arg)
        if _is_sensitive_flag(arg):
            skip_next = True
    return redacted


def _is_sensitive_flag(arg: str) -> bool:
    lowered = arg.lstrip("-").lower()
    return any(part in lowered for part in _SENSITIVE_FLAG_PARTS)


__all__ = ["run_with_io_logging"]
