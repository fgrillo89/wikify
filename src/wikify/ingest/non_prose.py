"""Deterministic labels for non-prose corpus chunks."""

from __future__ import annotations

import re

from .section_classifier import SectionType, classify_section_path

_FIGURE_CAPTION_RE = re.compile(
    r"^\W*(fig(?:ure)?|scheme|schematic)\.?\s*\d+[a-z]?\b",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(
    r"^\W*table\.?\s*\d+[a-z]?\b",
    re.IGNORECASE,
)


def classify_chunk_kind(
    text: str,
    section_path: list[str] | None,
    *,
    is_boilerplate: bool = False,
) -> str:
    """Return the persisted ``section_type`` for one chunk."""
    if is_boilerplate:
        return SectionType.BOILERPLATE.value

    path = list(section_path or [])
    if path and path[0] == "__image__":
        return SectionType.CAPTION.value

    stripped = text.strip()
    if _is_markdown_table(stripped):
        return SectionType.TABLE.value
    if _TABLE_CAPTION_RE.match(stripped):
        return SectionType.TABLE.value
    if _FIGURE_CAPTION_RE.match(stripped):
        return SectionType.FIGURE.value

    return classify_section_path(path).value


def _is_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = [line for line in lines if line.count("|") >= 2]
    if len(pipe_lines) < 2:
        return False
    separator_seen = any(
        re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line)
        for line in pipe_lines
    )
    return separator_seen or len(pipe_lines) / len(lines) >= 0.8
