"""Tests for wikify.bundle.run.lock — file-lock semantics with TTL."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wikify.api import Bundle
from wikify.bundle.run.lock import (
    LockHeldError,
    acquire_lock,
    read_lock,
    release_lock,
    run_lock,
)


def _bundle(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle(root=tmp_path)


def test_acquire_writes_lock(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a", ttl_seconds=60)
    record = read_lock(bundle)
    assert record is not None
    assert record["owner"] == "a"
    assert record["ttl_seconds"] == 60


def test_release_removes_lock(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a")
    release_lock(bundle)
    assert read_lock(bundle) is None


def test_release_idempotent(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    release_lock(bundle)  # no-op when no lock
    release_lock(bundle)  # still no-op


def test_contention_raises_lock_held(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a", ttl_seconds=60)
    with pytest.raises(LockHeldError) as exc:
        acquire_lock(bundle, owner="b", ttl_seconds=60)
    assert exc.value.owner == "a"
    assert exc.value.acquired_at


def test_force_overrides_held_lock(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a", ttl_seconds=60)
    displaced = acquire_lock(bundle, owner="b", ttl_seconds=60, force=True)
    assert displaced is not None
    assert displaced["owner"] == "a"
    assert read_lock(bundle)["owner"] == "b"


def test_stale_lock_silently_reclaimed(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    # Write an expired lock by hand.
    expired = (datetime.now(UTC) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bundle.lock_path.write_text(
        json.dumps(
            {
                "owner": "ghost",
                "acquired_at": expired,
                "expires_at": expired,
                "ttl_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    # No exception even without force=True — the stale lock is reclaimed.
    acquire_lock(bundle, owner="b", ttl_seconds=60)
    assert read_lock(bundle)["owner"] == "b"


def test_run_lock_context(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    with run_lock(bundle, owner="a", ttl_seconds=60):
        assert read_lock(bundle)["owner"] == "a"
    assert read_lock(bundle) is None


def test_run_lock_releases_on_exception(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        with run_lock(bundle, owner="a", ttl_seconds=60):
            raise RuntimeError("boom")
    assert read_lock(bundle) is None


def test_release_lock_with_owner_no_ops_when_not_owner(tmp_path: Path) -> None:
    """release_lock(owner=...) must not delete a lock owned by someone else.

    Guards against the post-TTL race where our finally-block fires after
    another process has already reclaimed the stale lock.
    """
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="winner", ttl_seconds=60)
    release_lock(bundle, owner="loser")  # different owner: must be no-op
    record = read_lock(bundle)
    assert record is not None
    assert record["owner"] == "winner"


# --- dead-PID stale-lock reclaim -------------------------------------------

import os  # noqa: E402
import socket  # noqa: E402

from wikify.bundle.run.lock import _owner_pid_dead  # noqa: E402

_HOST = socket.gethostname()
_FAR_FUTURE = "2999-01-01T00:00:00Z"


def test_acquire_writes_host(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a", ttl_seconds=60)
    assert read_lock(bundle)["host"] == _HOST


def test_owner_pid_dead_matrix() -> None:
    assert _owner_pid_dead({"pid": 999999, "host": _HOST}) is True
    assert _owner_pid_dead({"pid": os.getpid(), "host": _HOST}) is False
    assert _owner_pid_dead({"pid": 999999, "host": "elsewhere"}) is False
    assert _owner_pid_dead({"pid": 999999}) is False


def test_dead_pid_lock_reclaimed_despite_valid_ttl(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle.lock_path.write_text(
        json.dumps({
            "owner": "ghost", "pid": 999999, "host": _HOST,
            "acquired_at": _FAR_FUTURE, "expires_at": _FAR_FUTURE,
            "ttl_seconds": 86400,
        }),
        encoding="utf-8",
    )
    acquire_lock(bundle, owner="b", ttl_seconds=60)
    assert read_lock(bundle)["owner"] == "b"


def test_release_lock_with_owner_unlinks_when_owner_matches(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    acquire_lock(bundle, owner="a", ttl_seconds=60)
    release_lock(bundle, owner="a")
    assert read_lock(bundle) is None
