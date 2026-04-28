"""Output format selection for corpus and wiki CLI surfaces.

Three concrete formats plus an ``auto`` sentinel:

- ``quiet``    one short handle per line; nothing else. Pipe-safe.
- ``compact``  tab-separated columns: score / metric / handle / title.
- ``json``     existing JSON shape (per-command schema).

``auto`` resolves to:

1. The value of ``WIKIFY_CLI_FORMAT`` if set to a concrete format.
   Lets agents force compact for inspection without per-call flags.
2. ``compact`` when stdout is a TTY.
3. ``quiet`` otherwise (pipe-safe — keeps ``find ... | traverse ...`` working).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

FormatChoice = str  # Literal["auto", "quiet", "compact", "json"]

CONCRETE_FORMATS: tuple[str, ...] = ("quiet", "compact", "json")
VALID_FORMATS: tuple[str, ...] = ("auto", *CONCRETE_FORMATS)

_ENV_FORMAT = "WIKIFY_CLI_FORMAT"


class FormatError(ValueError):
    """Raised when ``--format`` is set to a value outside :data:`VALID_FORMATS`."""


def resolve_format(choice: str) -> str:
    """Resolve ``--format`` to a concrete format name.

    Raises :class:`FormatError` (a ``ValueError`` subclass) for unknown
    values; CLI handlers catch it and translate to a structured envelope.
    """
    if choice not in VALID_FORMATS:
        raise FormatError(
            f"unknown --format {choice!r}; expected one of {', '.join(VALID_FORMATS)}"
        )
    if choice != "auto":
        return choice
    env_choice = os.environ.get(_ENV_FORMAT)
    if env_choice in CONCRETE_FORMATS:
        return env_choice
    return "compact" if sys.stdout.isatty() else "quiet"


def format_row(columns: Iterable[str]) -> str:
    """Tab-separated row. Stable for shell ``cut -f<n>``."""
    return "\t".join(str(c) for c in columns)


__all__ = [
    "CONCRETE_FORMATS",
    "VALID_FORMATS",
    "FormatError",
    "format_row",
    "resolve_format",
]
