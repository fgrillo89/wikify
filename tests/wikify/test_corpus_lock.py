"""Tests for ``wikify.corpus.lock``."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wikify.api import Corpus
from wikify.corpus.lock import (
    CorpusLockHeldError,
    acquire_lock,
    corpus_lock,
    read_lock,
    release_lock,
)


def _corpus(tmp_path: Path) -> Corpus:
    return Corpus(root=tmp_path / "corpus")


def test_acquire_creates_lock_file(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="t")
    p = c.root / ".ingest.lock"
    assert p.exists()
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["owner"] == "t"
    assert rec["pid"] == os.getpid()
    assert rec["root"] == str(c.root)


def test_second_acquire_raises(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="a")
    with pytest.raises(CorpusLockHeldError) as exc_info:
        acquire_lock(c, owner="b")
    assert exc_info.value.owner == "a"
    assert exc_info.value.path == c.root / ".ingest.lock"


def test_stale_lock_reclaimed(tmp_path: Path) -> None:
    """Lock past its expires_at is silently overwritten by next acquire."""
    c = _corpus(tmp_path)
    c.root.mkdir(parents=True, exist_ok=True)
    expired = (datetime.now(UTC) - timedelta(seconds=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )
    (c.root / ".ingest.lock").write_text(
        json.dumps({
            "owner": "old",
            "pid": 99999,
            "acquired_at": expired,
            "expires_at": expired,
            "ttl_seconds": 1,
            "root": str(c.root),
        }),
        encoding="utf-8",
    )
    acquire_lock(c, owner="new")
    rec = read_lock(c)
    assert rec is not None
    assert rec["owner"] == "new"


def test_stale_lock_via_short_ttl(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="a", ttl_seconds=1)
    time.sleep(1.5)
    acquire_lock(c, owner="b")
    rec = read_lock(c)
    assert rec is not None
    assert rec["owner"] == "b"


def test_release_only_removes_owners_lock(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="a")
    # Different owner — must not touch the file.
    release_lock(c, owner="b")
    assert (c.root / ".ingest.lock").exists()
    release_lock(c, owner="a")
    assert not (c.root / ".ingest.lock").exists()


def test_release_unconditional_when_owner_none(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="a")
    release_lock(c, owner=None)
    assert not (c.root / ".ingest.lock").exists()


def test_context_manager_releases_on_exit(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    with corpus_lock(c, owner="ctx"):
        rec = read_lock(c)
        assert rec is not None
        assert rec["owner"] == "ctx"
    assert read_lock(c) is None


def test_context_manager_releases_on_exception(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    with pytest.raises(ValueError):  # noqa: PT011
        with corpus_lock(c, owner="ctx"):
            raise ValueError("boom")
    assert read_lock(c) is None


def test_force_overrides_live_lock(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="a")
    acquire_lock(c, owner="b", force=True)
    rec = read_lock(c)
    assert rec is not None
    assert rec["owner"] == "b"


# --- dead-PID stale-lock reclaim -------------------------------------------

import socket  # noqa: E402

from wikify.corpus.lock import _owner_pid_dead, _proc_started_at  # noqa: E402

_HOST = socket.gethostname()
_FAR_FUTURE = "2999-01-01T00:00:00Z"


def _write_lock(c: Corpus, **fields: object) -> None:
    c.root.mkdir(parents=True, exist_ok=True)
    rec = {
        "owner": "old", "acquired_at": _FAR_FUTURE,
        "expires_at": _FAR_FUTURE, "ttl_seconds": 86400, "root": str(c.root),
    }
    rec.update(fields)
    (c.root / ".ingest.lock").write_text(json.dumps(rec), encoding="utf-8")


def test_acquire_writes_host(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="t")
    assert read_lock(c)["host"] == _HOST


def test_dead_pid_same_host_is_stale() -> None:
    assert _owner_pid_dead({"pid": 999999, "host": _HOST}) is True


def test_live_pid_same_host_not_stale() -> None:
    assert _owner_pid_dead({"pid": os.getpid(), "host": _HOST}) is False


def test_dead_pid_other_host_not_judged() -> None:
    # A pid from another machine says nothing about our process table.
    assert _owner_pid_dead({"pid": 999999, "host": "some-other-host"}) is False


def test_missing_host_or_pid_not_judged() -> None:
    assert _owner_pid_dead({"pid": 999999}) is False
    assert _owner_pid_dead({"host": _HOST}) is False


def test_dead_pid_lock_reclaimed_despite_valid_ttl(tmp_path: Path) -> None:
    """A dead owner's lock is reclaimed even though its TTL has not expired."""
    c = _corpus(tmp_path)
    _write_lock(c, pid=999999, host=_HOST)
    acquire_lock(c, owner="new")
    assert read_lock(c)["owner"] == "new"


def test_live_pid_lock_held_despite_request(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    _write_lock(c, pid=os.getpid(), host=_HOST, started_at=_proc_started_at())
    with pytest.raises(CorpusLockHeldError):
        acquire_lock(c, owner="intruder")


def test_acquire_writes_started_at(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    acquire_lock(c, owner="t")
    # psutil is a hard dep, so start time is always recorded here.
    assert isinstance(read_lock(c)["started_at"], (int, float))


def test_reused_pid_is_not_the_original_owner() -> None:
    """A live pid whose start time differs from the record is a recycled pid."""
    # Same pid (ours, alive) but a start time from far in the past ->
    # the recorded owner is gone, the pid was reused.
    rec = {"pid": os.getpid(), "host": _HOST, "started_at": 1.0}
    assert _owner_pid_dead(rec) is True


def test_matching_pid_and_start_time_is_live() -> None:
    rec = {
        "pid": os.getpid(), "host": _HOST, "started_at": _proc_started_at(),
    }
    assert _owner_pid_dead(rec) is False
