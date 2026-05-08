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
