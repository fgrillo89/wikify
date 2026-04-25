"""``run/lock`` — bundle-wide advisory lock with TTL.

Ports the legacy ``session.acquire_lock`` semantics to the v2 bundle
layout. Single global lock per run for now (per-concept claims land in
W4). Stale locks (TTL expired) are silently reclaimed. Live locks held
by a different owner raise :class:`LockHeldError`, which the CLI
translates to ``EXIT_LOCK_HELD`` (exit code 2) via ``cli/_helpers.py``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from ...api import Bundle


class LockHeldError(RuntimeError):
    """Raised when ``acquire_lock`` finds the lock held by a live owner."""

    def __init__(self, owner: str, acquired_at: str) -> None:
        super().__init__(f"lock held by {owner!r} since {acquired_at}")
        self.owner = owner
        self.acquired_at = acquired_at


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _is_stale(record: dict) -> bool:
    expires_s = record.get("expires_at")
    if expires_s:
        expires = _parse_iso(expires_s)
        if expires is None:
            return False
        return datetime.now(UTC) > expires
    ttl = record.get("ttl_seconds")
    acquired_s = record.get("acquired_at")
    if not (ttl and acquired_s):
        return False
    acquired = _parse_iso(acquired_s)
    if acquired is None:
        return False
    return datetime.now(UTC) > acquired + timedelta(seconds=int(ttl))


def read_lock(bundle: Bundle) -> dict | None:
    """Return the lock record dict, or ``None`` if no lock file exists."""
    if not bundle.lock_path.exists():
        return None
    try:
        return json.loads(bundle.lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def acquire_lock(
    bundle: Bundle,
    owner: str,
    *,
    ttl_seconds: int = 3600,
    force: bool = False,
) -> dict | None:
    """Acquire ``<bundle>/run/lock`` or raise :class:`LockHeldError`.

    A lock whose ``expires_at`` (or implied TTL window) has passed is
    treated as stale and silently reclaimed. ``force=True`` overwrites
    a live lock and returns the displaced record.
    """
    bundle.run_dir.mkdir(parents=True, exist_ok=True)
    displaced: dict | None = None
    existing = read_lock(bundle)
    if existing and not _is_stale(existing):
        if not force:
            raise LockHeldError(
                existing.get("owner", "unknown"),
                existing.get("acquired_at", ""),
            )
        displaced = existing
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    record = {
        "owner": owner,
        "pid": os.getpid(),
        "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": ttl_seconds,
    }
    bundle.lock_path.write_text(json.dumps(record), encoding="utf-8")
    return displaced


def release_lock(bundle: Bundle) -> None:
    """Remove the lock file unconditionally."""
    if bundle.lock_path.exists():
        bundle.lock_path.unlink()


@contextmanager
def run_lock(bundle: Bundle, owner: str, *, ttl_seconds: int = 3600):
    """Context manager that holds the lock for the duration of the block.

    Usage::

        with run_lock(bundle, owner="agent-1"):
            ...mutations...
    """
    acquire_lock(bundle, owner=owner, ttl_seconds=ttl_seconds)
    try:
        yield
    finally:
        release_lock(bundle)
