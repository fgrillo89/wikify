"""``work/inbox/*.jsonl`` — append-only cross-talk channels.

Four kinds (one file per kind):

- ``evidence_suggestions.jsonl`` — "this chunk is relevant to that concept"
- ``concept_suggestions.jsonl``  — "create a new concept about X"
- ``merge_suggestions.jsonl``    — "concept A and concept B are the same"
- ``query_feedback.jsonl``       — "the wiki couldn't answer this question"

Workers append; the consolidator (``work tend``) drains and applies.
The single-file-per-kind shape is fine for sequential workflows; a
per-writer split can land later without changing this module's API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Literal

from ...api import Bundle

InboxKind = Literal[
    "evidence_suggestions",
    "concept_suggestions",
    "merge_suggestions",
    "query_feedback",
]

_VALID_KINDS = {
    "evidence_suggestions",
    "concept_suggestions",
    "merge_suggestions",
    "query_feedback",
}


def _validate_kind(kind: str) -> None:
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"unknown inbox kind {kind!r}; valid: {sorted(_VALID_KINDS)}"
        )


def inbox_path(bundle: Bundle, kind: str) -> Path:
    _validate_kind(kind)
    return bundle.work_inbox_dir / f"{kind}.jsonl"


def append_inbox(bundle: Bundle, kind: str, record: dict) -> None:
    """Append one suggestion record to ``work/inbox/<kind>.jsonl``."""
    _validate_kind(kind)
    p = inbox_path(bundle, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def append_inbox_records(
    bundle: Bundle, kind: str, records: Iterable[dict]
) -> int:
    """Append many records in one open. Returns count appended."""
    _validate_kind(kind)
    p = inbox_path(bundle, kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
            n += 1
    return n


def read_inbox(bundle: Bundle, kind: str) -> list[dict]:
    """Read every record from one inbox kind. Skips unparseable lines."""
    _validate_kind(kind)
    p = inbox_path(bundle, kind)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def list_inbox_files(bundle: Bundle) -> list[str]:
    """Return the names of inbox files present, sorted."""
    if not bundle.work_inbox_dir.is_dir():
        return []
    return sorted(
        p.name for p in bundle.work_inbox_dir.iterdir() if p.is_file()
    )


def truncate_inbox(bundle: Bundle, kind: str) -> int:
    """Drop every record from one inbox kind. Returns the count removed."""
    _validate_kind(kind)
    records = read_inbox(bundle, kind)
    p = inbox_path(bundle, kind)
    if p.exists():
        p.unlink()
    return len(records)
