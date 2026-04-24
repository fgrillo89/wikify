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
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


def _lock_is_stale(record: dict) -> bool:
    """Return True if a lock record's expires_at is in the past."""
    expires = record.get("expires_at")
    if not expires:
        ttl = record.get("ttl_seconds")
        acquired = record.get("acquired_at")
        if not (ttl and acquired):
            return False
        try:
            acquired_dt = datetime.strptime(acquired, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return False
        return datetime.now(timezone.utc) > acquired_dt + timedelta(seconds=int(ttl))
    try:
        expires_dt = datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return datetime.now(timezone.utc) > expires_dt


def _lock_path_for(session_path: Path) -> Path:
    bundle_root = Path(json.loads(session_path.read_text(encoding="utf-8"))["bundle_root"])
    return BundlePaths(bundle_root).session_lock_path


def acquire_lock(session_path: Path, owner: str, ttl_seconds: int = 3600) -> None:
    """Acquire the session lock or raise SessionLockHeldError.

    A lock whose `expires_at` has passed is treated as stale and silently
    reclaimed. Otherwise the existing owner holds and acquisition fails.
    """
    lock_path = _lock_path_for(session_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if existing and not _lock_is_stale(existing):
            raise SessionLockHeldError(
                existing.get("owner", "unknown"), existing.get("acquired_at", "")
            )
    now_dt = datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(seconds=ttl_seconds)
    lock_path.write_text(
        json.dumps(
            {
                "owner": owner,
                "pid": os.getpid(),
                "acquired_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ttl_seconds": ttl_seconds,
            }
        ),
        encoding="utf-8",
    )


def release_lock(session_path: Path) -> None:
    lock_path = _lock_path_for(session_path)
    if lock_path.exists():
        lock_path.unlink()


def read_lock(session_path: Path) -> dict | None:
    """Return the lock record (or None if no lock file exists)."""
    lock_path = _lock_path_for(session_path)
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


RUN_SCHEMA_VERSION = 1


def write_run_snapshot(session: "SessionV1") -> Path:
    """Flush a final telemetry snapshot to <bundle>/_run.json.

    Called by `wikify session close`. The snapshot is session-derived —
    it does not include meter-only fields (per-call token counts, cache
    hit rates) because those live in _calls.jsonl and the skill path
    does not own a CostMeter. Parity against legacy `run_baseline`
    output is the future gate for the recorded-transcript test; today
    this writes a stable, schema_version-stamped minimum.
    """
    bundle_paths = BundlePaths(Path(session.bundle_root))
    bundle_paths.ensure()
    pages = [p.model_dump(mode="json") for p in session.pages]
    counts = {"planned": 0, "drafted": 0, "validated": 0, "committed": 0, "failed": 0}
    for entry in pages:
        status = entry.get("status", "planned")
        counts[status] = counts.get(status, 0) + 1
    snapshot = {
        "schema_version": RUN_SCHEMA_VERSION,
        "session_id": session.session_id,
        "strategy": session.strategy,
        "status": session.status,
        "bundle_root": session.bundle_root,
        "corpus_root": session.corpus_root,
        "created_at": session.created_at,
        "closed_at": session.updated_at,
        "budget_target_haiku_eq": session.budget.haiku_eq_target,
        "budget_spent_haiku_eq": session.budget.haiku_eq_spent,
        "stages": {
            name: stage.model_dump(mode="json")
            for name, stage in {
                "seed_selection": session.stages.seed_selection,
                "extract": session.stages.extract,
                "write": session.stages.write,
            }.items()
        },
        "config": session.config.model_dump(mode="json"),
        "pages": pages,
        "n_pages_committed": counts.get("committed", 0),
        "n_pages_failed": counts.get("failed", 0),
        "page_counts": counts,
        "telemetry_paths": session.telemetry_paths.model_dump(mode="json"),
    }
    run_path = bundle_paths.run_path
    run_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return run_path


@contextmanager
def session_lock(session_path: Path, owner: str, ttl_seconds: int = 3600):
    """Context manager: acquire the lock, run the block, release on exit.

    If acquisition fails the context never enters and SessionLockHeldError
    propagates to the caller. Release is best-effort — if the lock was
    already removed (e.g., stale-reclaimed elsewhere) the unlink is a no-op.
    """
    acquire_lock(session_path, owner=owner, ttl_seconds=ttl_seconds)
    try:
        yield
    finally:
        release_lock(session_path)
