"""Shared helpers for the wikify skill-driven CLI families.

Every `cli_cmds/*` module repeats the same patterns:

- A structured JSON error envelope to stderr followed by `typer.Exit(code)`.
- A `SessionLockHeldError` -> `{"error": "lock_held", ...}` translation.
- A `_cli_owner(override)` lock-owner string.
- Stripping the `schema_version` envelope field before Pydantic validation.

Centralising these here removes ~85 lines of duplication across the
seven sub-apps and keeps the error contract uniform.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, NoReturn

import typer

from ..session import SessionLockHeldError


def cli_owner(override: str | None) -> str:
    """Return the canonical CLI lock-owner string.

    `--owner` flag overrides; otherwise falls back to a pid-tagged
    default. Every command that takes the session lock should call
    this rather than constructing its own.
    """
    return override or f"wikify-cli/pid-{os.getpid()}"


def cli_error(code: int, **fields: Any) -> NoReturn:
    """Emit a structured JSON error envelope on stderr and exit.

    Conventions:
      - `ok` is unconditionally forced to False (callers cannot override it
        even by accident; the spread happens BEFORE the ok assignment).
      - `error` should be a stable, machine-readable code (snake_case).
      - All other fields are passed through as-is.

    Never returns. Always raises `typer.Exit(code)`.
    """
    payload: dict[str, Any] = {**fields, "ok": False}
    typer.echo(json.dumps(payload), err=True)
    raise typer.Exit(code=code)


def lock_held(exc: SessionLockHeldError) -> NoReturn:
    """Emit the canonical lock_held envelope and exit with code 2.

    Preserves the original `__cause__` chain (`raise ... from exc`) so the
    Python traceback retains the SessionLockHeldError as the root cause —
    matches the pre-helper inline pattern that every cli_cmds/* module
    used.
    """
    payload = {
        "ok": False,
        "error": "lock_held",
        "owner": exc.owner,
        "acquired_at": exc.acquired_at,
    }
    typer.echo(json.dumps(payload), err=True)
    raise typer.Exit(code=2) from exc


@contextmanager
def handle_lock_held():
    """Context manager that translates SessionLockHeldError into the canonical
    CLI envelope.

    Use as:

        with handle_lock_held():
            with session_lock(session_path, owner=cli_owner(override)):
                ...

    Any `SessionLockHeldError` raised inside the inner `session_lock`
    surfaces as the structured stderr envelope and a `typer.Exit(2)`.

    Defensive: if `lock_held()` ever returned without raising (e.g., a
    test-time monkeypatch that elides the exit), the unreachable
    `AssertionError` would surface the contract violation rather than
    let the caller proceed with stale/undefined state.
    """
    try:
        yield
    except SessionLockHeldError as exc:
        lock_held(exc)
        raise AssertionError("unreachable: lock_held() must not return")  # noqa: TRY004,B904


def strip_envelope(data: dict, *fields: str) -> dict:
    """Return a copy of `data` with the listed envelope fields removed.

    Defaults to stripping just `schema_version`, which is the
    convention all scratch artifacts use to carry an on-disk format
    version separately from the canonical Pydantic models (which are
    `extra="forbid"`).
    """
    if not fields:
        fields = ("schema_version",)
    blocked = set(fields)
    return {k: v for k, v in data.items() if k not in blocked}


__all__ = [
    "cli_owner",
    "cli_error",
    "lock_held",
    "handle_lock_held",
    "strip_envelope",
]
