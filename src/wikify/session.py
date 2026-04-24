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
from .types import Role

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
    haiku_eq_spent: float = 0.0


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
    status: Literal["active", "completed", "failed", "abandoned"] = "active"
    created_at: str
    updated_at: str
    budget: Budget = Field(default_factory=Budget)
    stages: Stages = Field(default_factory=Stages)
    pages: list[PageEntry] = Field(default_factory=list)
    config: BaselineConfig = Field(default_factory=BaselineConfig)
    telemetry_paths: TelemetryPaths
    # Populated by `wikify kg seeds --persist`. Carried forward into
    # _run.json so the skill-path snapshot can match the legacy
    # `seed_doc_ids` / `seed_chunks_read` fields on session close.
    seed_doc_ids: list[str] = Field(default_factory=list)
    seed_chunk_ids: list[str] = Field(default_factory=list)
    # Per-iteration counter for campaign-style reruns. Baseline keeps
    # the default "create"; scripted/guided bump it between iterations.
    iteration: str = "create"


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


def acquire_lock(
    session_path: Path,
    owner: str,
    ttl_seconds: int = 3600,
    *,
    force: bool = False,
) -> dict | None:
    """Acquire the session lock or raise SessionLockHeldError.

    A lock whose `expires_at` has passed is treated as stale and silently
    reclaimed. Otherwise the existing owner holds and acquisition fails,
    unless `force=True` — in which case the existing record is overwritten
    and returned so the caller can log the displaced owner.
    """
    lock_path = _lock_path_for(session_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    displaced: dict | None = None
    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        if existing and not _lock_is_stale(existing):
            if not force:
                raise SessionLockHeldError(
                    existing.get("owner", "unknown"), existing.get("acquired_at", "")
                )
            displaced = existing
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
    return displaced


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

    Called by `wikify session close`. Aggregates:
      - session state (strategy, stages, pages, budget, config)
      - legacy-shape overlay fields derived from config + scratch drafts
      - meter-derived fields read from <bundle>/_calls.jsonl (Tier 1
        item 3): run_id, calls, spent_haiku_eq, cache_hits, context_used_max,
        wall_seconds, input_tokens, output_tokens

    This is the full Phase 5 deletion-gate shape — downstream consumers
    `wikify html` and `wikify eval` read exactly this envelope whether
    the bundle came from legacy `run_baseline()` or the skill path.
    """
    bundle_paths = BundlePaths(Path(session.bundle_root))
    bundle_paths.ensure()
    pages = [p.model_dump(mode="json") for p in session.pages]
    counts = {"planned": 0, "drafted": 0, "validated": 0, "committed": 0, "failed": 0}
    for entry in pages:
        status = entry.get("status", "planned")
        counts[status] = counts.get(status, 0) + 1

    # Legacy-shape overlay fields. These mirror run_baseline()'s
    # snapshot writer so existing downstream consumers work without
    # rework. Values derived from session state + config + on-disk
    # scratch artifacts so no CostMeter is required.
    config = session.config
    budget_target = float(session.budget.haiku_eq_target or 0)
    write_fraction = float(config.baseline_write_fraction)
    extract_fraction = 1.0 - write_fraction
    extract_budget = budget_target * extract_fraction
    write_budget = budget_target * write_fraction
    seed_extract_budget = extract_budget * float(config.abstract_fraction)

    evidence_chunks_read = _gather_evidence_chunks_from_scratch(bundle_paths.scratch_dir)

    # Meter aggregates from _calls.jsonl. Absent file is fine for
    # sessions that never dispatched a model call.
    meter_fields = _aggregate_calls_jsonl(bundle_paths.calls_path)

    skipped_thin_pages = [
        {"page_id": p["page_id"], "status": p.get("status")}
        for p in pages
        if p.get("status") == "failed"
    ]
    write_rejections = [
        {
            "page_id": p["page_id"],
            "validation_path": p.get("validation_path"),
        }
        for p in pages
        if p.get("status") == "failed"
    ]

    # Per-close run_id so session resume + second close produces a
    # distinct run_id, matching legacy semantics where every
    # `run_baseline()` invocation mints a fresh id. Downstream consumers
    # that join on run_id don't see collisions.
    run_id = f"{session.session_id}-{session.updated_at}"

    snapshot = {
        "schema_version": RUN_SCHEMA_VERSION,
        "session_id": session.session_id,
        "run_id": run_id,
        "strategy": session.strategy,
        "mode": session.strategy,
        "iteration": session.iteration,
        "status": session.status,
        "bundle_root": session.bundle_root,
        "corpus_root": session.corpus_root,
        "created_at": session.created_at,
        "closed_at": session.updated_at,
        "timestamp_utc": session.updated_at,
        "budget_target_haiku_eq": budget_target,
        "budget_spent_haiku_eq": session.budget.haiku_eq_spent,
        "seed_doc_ids": list(session.seed_doc_ids),
        "seed_chunks_read": list(session.seed_chunk_ids),
        "evidence_chunks_read": evidence_chunks_read,
        "split_initial": {
            "extract_haiku_eq": extract_budget,
            "write_haiku_eq": write_budget,
            "curate_haiku_eq": 0.0,
        },
        "seed_extract_budget": seed_extract_budget,
        "baseline_write_fraction": write_fraction,
        "min_evidence_chunks": 0,
        "skipped_thin_pages": skipped_thin_pages,
        "n_pages_written": counts.get("committed", 0),
        "n_pages_committed": counts.get("committed", 0),
        "n_pages_failed": counts.get("failed", 0),
        "write_rejections": write_rejections,
        "page_counts": counts,
        "stages": {
            name: stage.model_dump(mode="json")
            for name, stage in {
                "seed_selection": session.stages.seed_selection,
                "extract": session.stages.extract,
                "write": session.stages.write,
            }.items()
        },
        "config": config.model_dump(mode="json"),
        "pages": pages,
        "telemetry_paths": session.telemetry_paths.model_dump(mode="json"),
        # Meter-derived fields matching legacy CostMeter.snapshot() shape.
        # Values are aggregated from <bundle>/_calls.jsonl; a session
        # that never recorded any call sees zeros but the fields are
        # still present so downstream consumers get a stable envelope.
        "budget_used_haiku_eq": meter_fields["budget_used_haiku_eq"],
        "wall_seconds": meter_fields["wall_seconds"],
        "by_role": meter_fields["by_role"],
        "by_tier": meter_fields["by_tier"],
        "context": meter_fields["context"],
        "calls": meter_fields["calls"],
        "cache_hit_rate": meter_fields["cache_hit_rate"],
    }
    run_path = bundle_paths.run_path
    run_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    # Append to _run_history.jsonl so multiple closes on the same
    # session (resume workflows, campaign iterations) leave an audit
    # trail instead of clobbering each other. Mirrors
    # distill/iteration.py's per-iteration history append.
    history_path = bundle_paths.run_history_path
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot) + "\n")
    return run_path


_EMPTY_AGG = {
    "calls": 0,
    "haiku_eq": 0.0,
    "wall_seconds": 0.0,
    "cache_hits": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "context_used_max": 0,
    "context_used_sum": 0,
    "headroom_min": 1 << 30,
    "headroom_sum": 0,
}


def _agg_to_dict(agg: dict) -> dict:
    """Mirror legacy meter._Aggregates.to_dict() output."""
    calls = agg["calls"]
    if calls == 0:
        return {"calls": 0}
    return {
        "calls": calls,
        "haiku_eq": agg["haiku_eq"],
        "wall_seconds": agg["wall_seconds"],
        "cache_hit_rate": agg["cache_hits"] / calls,
        "input_tokens": agg["input_tokens"],
        "output_tokens": agg["output_tokens"],
        "context_used_max": agg["context_used_max"],
        "context_used_mean": agg["context_used_sum"] / calls,
        "headroom_min": agg["headroom_min"],
        "headroom_mean": agg["headroom_sum"] / calls,
    }


def _update_agg(agg: dict, record: dict) -> None:
    agg["calls"] += 1
    agg["haiku_eq"] += float(record.get("haiku_eq", 0.0) or 0.0)
    agg["wall_seconds"] += float(record.get("wall_seconds", 0.0) or 0.0)
    if record.get("cache_hit"):
        agg["cache_hits"] += 1
    agg["input_tokens"] += int(record.get("input_tokens", 0) or 0)
    agg["output_tokens"] += int(record.get("output_tokens", 0) or 0)
    context_used = int(record.get("context_used", 0) or 0)
    context_cap = int(record.get("context_cap", 0) or 0)
    agg["context_used_max"] = max(agg["context_used_max"], context_used)
    agg["context_used_sum"] += context_used
    headroom = context_cap - context_used
    agg["headroom_min"] = min(agg["headroom_min"], headroom)
    agg["headroom_sum"] += headroom


class UnknownRoleError(ValueError):
    """A _calls.jsonl record named a role not in the Role enum."""


def _initial_by_role() -> dict[str, dict]:
    """Pre-populate by_role with every legacy Role enum value.

    Matches legacy `CostMeter.__init__` which seeds `self._by_role` with
    `{r: _Aggregates() for r in Role}`. A write-only run therefore emits
    all five role keys (four with `{"calls": 0}`) rather than a single
    `{"writer": ...}` entry — downstream consumers can rely on the key
    set being stable regardless of which roles were actually exercised.
    """
    return {role.value: dict(_EMPTY_AGG) for role in Role}


def _aggregate_calls_jsonl(calls_path: Path) -> dict:
    """Read _calls.jsonl and produce the legacy CostMeter.snapshot shape."""
    total = dict(_EMPTY_AGG)
    by_role: dict[str, dict] = _initial_by_role()
    by_tier: dict[str, dict] = {}
    if not calls_path.is_file():
        return {
            "budget_used_haiku_eq": 0.0,
            "wall_seconds": 0.0,
            "by_role": {k: _agg_to_dict(v) for k, v in by_role.items()},
            "by_tier": {},
            "context": {
                "used_max": 0,
                "used_mean": 0,
                "headroom_min": 0,
                "headroom_mean": 0,
            },
            "calls": 0,
            "cache_hit_rate": 0.0,
        }
    known_roles = {role.value for role in Role}
    for line in calls_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        role_key = str(record.get("role", ""))
        if role_key not in known_roles:
            raise UnknownRoleError(
                f"_calls.jsonl record has unknown role {role_key!r}; "
                f"expected one of {sorted(known_roles)}"
            )
        tier_key = str(record.get("tier", "unknown"))
        _update_agg(total, record)
        _update_agg(by_role[role_key], record)
        _update_agg(by_tier.setdefault(tier_key, dict(_EMPTY_AGG)), record)
    calls = total["calls"]
    return {
        "budget_used_haiku_eq": total["haiku_eq"],
        "wall_seconds": total["wall_seconds"],
        "by_role": {k: _agg_to_dict(v) for k, v in by_role.items()},
        "by_tier": {k: _agg_to_dict(v) for k, v in by_tier.items()},
        "context": {
            "used_max": total["context_used_max"],
            "used_mean": total["context_used_sum"] / calls if calls else 0,
            "headroom_min": total["headroom_min"] if calls else 0,
            "headroom_mean": total["headroom_sum"] / calls if calls else 0,
        },
        "calls": calls,
        "cache_hit_rate": total["cache_hits"] / calls if calls else 0.0,
    }


def _gather_evidence_chunks_from_scratch(scratch_dir: Path) -> list[str]:
    """Union of evidence_v2[*].chunk_id across every draft in scratch.

    Preserves insertion order across drafts; duplicates are dropped.
    Returns empty list when scratch_dir is missing or has no drafts.
    """
    if not scratch_dir.is_dir():
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for path in sorted(scratch_dir.glob("draft-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for entry in data.get("evidence_v2", []) or []:
            cid = entry.get("chunk_id")
            if cid and cid not in seen:
                seen.add(cid)
                ordered.append(cid)
    return ordered


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
