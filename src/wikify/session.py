"""Durable run-session object for the skill-driven workflow.

The session is the coordination point between skill workflows, CLI tools,
and model-calling subagents. It lives on disk at
``<bundle>/_session/session.json``; mutations go through CLI commands so
the agent never hand-edits canonical fields.

Schema: v1. Baseline strategy subset. See
``.claude/skills/wikify/reference/schemas.md``.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .paths import BundlePaths

SCHEMA_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StageState(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: str | None = None
    finished_at: str | None = None


class PageEntry(BaseModel):
    model_config = {"extra": "forbid"}
    page_id: str
    status: Literal["planned", "drafted", "validated", "committed", "failed"] = "planned"
    draft_path: str | None = None
    validation_path: str | None = None


class Budget(BaseModel):
    model_config = {"extra": "forbid"}
    haiku_eq_target: int = 0
    haiku_eq_spent: int = 0


class BaselineConfig(BaseModel):
    model_config = {"extra": "forbid"}
    baseline_write_fraction: float = 0.35
    abstract_fraction: float = 0.60
    top_k: int = 8
    default_tiers: dict[str, str] = Field(
        default_factory=lambda: {"extract": "S", "write": "M", "escalate": "L"}
    )


class TelemetryPaths(BaseModel):
    model_config = {"extra": "forbid"}
    run_path: str
    calls_path: str


class Stages(BaseModel):
    model_config = {"extra": "forbid"}
    seed_selection: StageState = Field(default_factory=StageState)
    extract: StageState = Field(default_factory=StageState)
    write: StageState = Field(default_factory=StageState)


class SessionV1(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = 1
    session_id: str
    strategy: Literal["baseline", "scripted-E", "scripted-M", "scripted-X", "guided"]
    bundle_root: str
    corpus_root: str
    status: Literal["active", "closed", "failed"] = "active"
    created_at: str
    updated_at: str
    budget: Budget = Field(default_factory=Budget)
    stages: Stages = Field(default_factory=Stages)
    pages: list[PageEntry] = Field(default_factory=list)
    config: BaselineConfig = Field(default_factory=BaselineConfig)
    telemetry_paths: TelemetryPaths


class SchemaVersionMismatchError(RuntimeError):
    """Raised when a session file's schema_version does not match the expected value."""


class SessionLockHeldError(RuntimeError):
    """Raised when a session lock is held by another owner."""

    def __init__(self, owner: str, acquired_at: str) -> None:
        super().__init__(f"session lock held by {owner!r} since {acquired_at}")
        self.owner = owner
        self.acquired_at = acquired_at


def load_session(session_path: Path) -> SessionV1:
    data = json.loads(session_path.read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"session {session_path}: schema_version={version!r}, expected {SCHEMA_VERSION}"
        )
    return SessionV1.model_validate(data)


def save_session(session_path: Path, session: SessionV1) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    payload = session.model_dump(mode="json")
    session_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def init_session(
    *,
    bundle_root: Path,
    corpus_root: Path,
    strategy: str = "baseline",
    budget_target_haiku_eq: int = 0,
) -> SessionV1:
    """Create the directory layout and return the new SessionV1 (not yet saved)."""
    paths = BundlePaths(Path(bundle_root))
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    paths.session_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    paths.scratch_dir.mkdir(parents=True, exist_ok=True)

    now = _utcnow()
    return SessionV1(
        session_id=uuid4().hex,
        strategy=strategy,  # type: ignore[arg-type]
        bundle_root=str(Path(bundle_root).resolve()),
        corpus_root=str(Path(corpus_root).resolve()),
        created_at=now,
        updated_at=now,
        budget=Budget(haiku_eq_target=budget_target_haiku_eq),
        telemetry_paths=TelemetryPaths(
            run_path=str(paths.run_path),
            calls_path=str(paths.calls_path),
        ),
    )


def touch(session: SessionV1) -> SessionV1:
    return session.model_copy(update={"updated_at": _utcnow()})


def apply_merge_patch(session: SessionV1, patch: dict) -> SessionV1:
    """Apply an RFC 7396 JSON Merge Patch to the session and return the new model.

    Only fields present in the patch are changed; nested dicts are merged
    recursively; explicit nulls delete.
    """
    base = session.model_dump(mode="json")
    merged = _merge_patch(base, patch)
    return SessionV1.model_validate(merged)


def _merge_patch(target: object, patch: object) -> object:
    if not isinstance(patch, dict):
        return patch
    if not isinstance(target, dict):
        target = {}
    out = dict(target)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = _merge_patch(out.get(key), value)
    return out


def checkpoint_session(session_path: Path, label: str) -> Path:
    session = load_session(session_path)
    paths = BundlePaths(Path(session.bundle_root))
    paths.session_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    dest = paths.session_checkpoints_dir / f"{label}.json"
    shutil.copyfile(session_path, dest)
    return dest


def acquire_lock(session_path: Path, owner: str, ttl_seconds: int = 3600) -> None:
    paths = BundlePaths(Path(json.loads(session_path.read_text())["bundle_root"]))
    lock_path = paths.session_lock_path
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        raise SessionLockHeldError(
            existing.get("owner", "unknown"), existing.get("acquired_at", "")
        )
    now = _utcnow()
    lock_path.write_text(
        json.dumps(
            {
                "owner": owner,
                "acquired_at": now,
                "pid": os.getpid(),
                "ttl_seconds": ttl_seconds,
            }
        ),
        encoding="utf-8",
    )


def release_lock(session_path: Path) -> None:
    paths = BundlePaths(Path(json.loads(session_path.read_text())["bundle_root"]))
    lock_path = paths.session_lock_path
    if lock_path.exists():
        lock_path.unlink()
