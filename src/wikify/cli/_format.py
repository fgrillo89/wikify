"""Output format selection for corpus and wiki CLI surfaces.

Four formats:

- ``quiet``    one short handle per line; nothing else. Pipe-safe.
- ``compact``  tab-separated columns: score / metric / handle / title.
                Default for TTY stdout; columns vary by command.
- ``table``    aligned columns + header. Human-friendly.
- ``json``     existing JSON shape (per-command schema).

When ``--format`` is left as the sentinel ``auto``, the actual mode is
``compact`` if stdout is a TTY else ``quiet``. This is what makes
``corpus find ... | corpus traverse ...`` work without flags.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

# Columns for compact/table mode. Each column is (header, width_hint).
# Widths are advisory — ``compact`` always emits tab-separated text.
FormatChoice = str  # Literal["auto", "quiet", "compact", "table", "json"]

VALID_FORMATS: tuple[str, ...] = ("auto", "quiet", "compact", "table", "json")


def resolve_format(choice: str) -> str:
    """Map ``auto`` to ``compact`` (TTY) or ``quiet`` (pipe). Pass through others."""
    if choice not in VALID_FORMATS:
        raise ValueError(
            f"unknown --format {choice!r}; expected one of {', '.join(VALID_FORMATS)}"
        )
    if choice != "auto":
        return choice
    return "compact" if sys.stdout.isatty() else "quiet"


def format_row(columns: Iterable[str]) -> str:
    """Tab-separated row. Stable for shell ``cut -f<n>``."""
    return "\t".join(str(c) for c in columns)


__all__ = ["VALID_FORMATS", "format_row", "resolve_format"]
