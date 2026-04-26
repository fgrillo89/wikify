"""Shared helpers for the v2 CLI nouns.

- ``cli_error(code, **fields)`` — emit a structured JSON envelope on
  stderr and exit with the given code. Used wherever a CLI handler
  surfaces a structured error to the agent.
- ``cli_owner(override)`` — canonical lock-owner string for the
  ``--owner`` CLI flag.
- ``strip_envelope(data, *fields)`` — drop scratch envelope keys
  (``schema_version`` by default) before Pydantic validation.
- ``EXIT_*`` constants per the redesign brief decision 6.
"""

from __future__ import annotations

import json
import os
from typing import Any, NoReturn

import typer


def cli_owner(override: str | None) -> str:
    """Return the canonical CLI lock-owner string.

    ``--owner`` overrides; otherwise falls back to a pid-tagged
    default. Every CLI handler that acquires a lock or claim should
    call this rather than constructing its own.
    """
    return override or f"wikify-cli/pid-{os.getpid()}"


def cli_error(code: int, **fields: Any) -> NoReturn:
    """Emit a structured JSON error envelope on stderr and exit.

    Conventions:
      - ``ok`` is unconditionally forced to False.
      - ``error`` should be a stable, machine-readable code (snake_case).
      - All other fields are passed through as-is.
    """
    payload: dict[str, Any] = {**fields, "ok": False}
    typer.echo(json.dumps(payload), err=True)
    raise typer.Exit(code=code)


def strip_envelope(data: dict, *fields: str) -> dict:
    """Return a copy of ``data`` with the listed envelope fields removed."""
    if not fields:
        fields = ("schema_version",)
    blocked = set(fields)
    return {k: v for k, v in data.items() if k not in blocked}


# Canonical CLI exit codes. Use these instead of magic numbers when calling
# ``cli_error(...)`` so the contract stays grep-able. Mapped per the redesign
# brief (decision 6 in docs/skill-centric-execution-plan.md).
EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_LOCK_HELD = 2
EXIT_BUDGET_EXCEEDED = 3
EXIT_STALE_CLAIM_BROKEN = 4


__all__ = [
    "EXIT_OK",
    "EXIT_VALIDATION",
    "EXIT_LOCK_HELD",
    "EXIT_BUDGET_EXCEEDED",
    "EXIT_STALE_CLAIM_BROKEN",
    "cli_owner",
    "cli_error",
    "strip_envelope",
]
