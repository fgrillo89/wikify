"""``run/state.json`` — the small mutable run-control file.

A slim subset of the legacy ``SessionV1``: identity, strategy, paths,
budget, stage status, run status. Concept memory belongs in
``work/concepts/<slug>/work.md``, not in run state. Aggregated cost and
event history are computed on demand from ``run/events.jsonl``; nothing
about them lives here.

See ``docs/filesystem-state-design.md`` (Monitoring surface) for the
canonical shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ...api import Bundle

SCHEMA_VERSION = 1

RunStatus = Literal["active", "completed", "failed", "abandoned"]
StageStatus = Literal["pending", "running", "done", "failed"]


def _utcnow() -> str:
    """ISO-8601 UTC timestamp, second-precision."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Budget(BaseModel):
    """Haiku-equivalent budget target + running spend."""

    target_haiku_eq: int = 0
    spent_haiku_eq: int = 0


class RunStateV1(BaseModel):
    """The contents of ``<bundle>/run/state.json``."""

    schema_version: int = SCHEMA_VERSION
    run_id: str
    status: RunStatus = "active"
    # Free-form workflow label (e.g. "baseline", "guided", "free", "query").
    # Passive metadata for replay + comparison; no Python branches on it.
    strategy: str = ""
    corpus_path: str
    wiki_path: str = "wiki"
    work_path: str = "work"
    budget: Budget = Field(default_factory=Budget)
    stages: dict[str, StageStatus] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)

    model_config = {"extra": "forbid"}


class SchemaVersionMismatchError(RuntimeError):
    """Raised when ``run/state.json`` is at a schema version we cannot read."""


def load_state(bundle: Bundle) -> RunStateV1:
    """Read ``<bundle>/run/state.json`` and return the parsed model."""
    text = bundle.state_path.read_text(encoding="utf-8")
    data = RunStateV1.model_validate_json(text)
    if data.schema_version != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"state.json at {bundle.state_path} has schema_version "
            f"{data.schema_version}, expected {SCHEMA_VERSION}"
        )
    return data


def save_state(bundle: Bundle, state: RunStateV1) -> None:
    """Atomically write ``state`` to ``<bundle>/run/state.json``.

    The write is via a sibling temp file + ``os.replace`` so a crashed
    writer never leaves a half-serialised state.json on disk.
    """
    import os
    import tempfile

    bundle.run_dir.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=str(bundle.run_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, bundle.state_path)
    except Exception:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def touch(state: RunStateV1) -> RunStateV1:
    """Return ``state`` with ``updated_at`` set to now."""
    return state.model_copy(update={"updated_at": _utcnow()})
