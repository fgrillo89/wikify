"""Corpus-wide advisory lock at ``<corpus>/.ingest.lock``.

Prevents two ``wikify corpus build`` calls (or any concurrent
``ingest_corpus`` invocations) from racing on the same output
directory. The atomic ``os.open(O_CREAT|O_EXCL)`` pattern guarantees
mutual exclusion across processes; stale locks (TTL expired) are
silently reclaimed.

Mirrors the bundle ``run/lock`` shape so the patterns line up.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..api import Corpus


class CorpusLockHeldError(RuntimeError):
    """Raised when ``acquire_lock`` finds the lock held by a live owner."""

    def __init__(self, owner: str, acquired_at: str, path: Path) -> None:
        super().__init__(
            f"corpus lock at {path} held by {owner!r} since {acquired_at}"
        )
        self.owner = owner
        self.acquired_at = acquired_at
        self.path = path


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _ttl_expired(record: dict) -> bool:
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


def _proc_started_at() -> float | None:
    """This process's creation time (epoch seconds), or None if unknowable."""
    try:
        import psutil

        return psutil.Process(os.getpid()).create_time()
    except Exception:  # noqa: BLE001
        return None


def _owner_pid_dead(record: dict) -> bool:
    """True if the lock names a pid on THIS host that is no longer alive.

    A killed ``corpus build`` leaves its lock behind with a 24h TTL; without
    this, the next run is blocked for a day on a process that no longer
    exists. Only judged when the recorded ``host`` matches ours -- a pid from
    another machine says nothing about our process table. Missing host/pid
    (older lock format) is 'cannot tell' -> not dead, so we never reclaim a
    lock we are unsure about.

    PID reuse: a recycled pid belongs to a different process with a different
    start time. When the lock carries ``started_at`` we treat the owner as
    dead unless the live pid's creation time still matches, so a reused pid
    does not masquerade as the original owner.
    """
    pid = record.get("pid")
    if not pid or record.get("host") != socket.gethostname():
        return False
    try:
        import psutil

        try:
            proc = psutil.Process(int(pid))
        except psutil.NoSuchProcess:
            return True  # owner gone
        started = record.get("started_at")
        if started is not None:
            try:
                if abs(proc.create_time() - float(started)) > 1.0:
                    return True  # pid reused by a different process
            except (psutil.Error, ValueError, TypeError):
                return False  # cannot verify identity -> do not reclaim
        return False  # same live process (or pre-started_at lock format)
    except Exception:  # noqa: BLE001 - if we can't tell, do not reclaim
        return False


def _is_stale(record: dict) -> bool:
    return _ttl_expired(record) or _owner_pid_dead(record)


def _lock_path(corpus: Corpus) -> Path:
    return corpus.root / ".ingest.lock"


def read_lock(corpus: Corpus) -> dict | None:
    """Return the lock record dict, or ``None`` if no lock file exists."""
    path = _lock_path(corpus)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_record(owner: str, ttl_seconds: int, root: Path) -> dict:
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    return {
        "owner": owner,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": _proc_started_at(),
        "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": ttl_seconds,
        "root": str(root),
    }


def _atomic_create(path: Path, payload: bytes) -> bool:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        return False
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return True


def _atomic_replace(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".ingest-lock-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def acquire_lock(
    corpus: Corpus,
    owner: str,
    *,
    ttl_seconds: int = 86400,
    force: bool = False,
) -> None:
    """Acquire ``<corpus>/.ingest.lock`` or raise ``CorpusLockHeldError``.

    Race-safe via ``O_EXCL`` for fresh acquisition and ``os.replace``
    + post-write owner verification for stale-lock reclaim. ``force=True``
    overrides a live lock without raising.

    The stale-reclaim path carries a narrow check->replace->readback window
    in which two processes simultaneously reclaiming the *same* stale lock
    could both verify their own write; this is an accepted trade-off of the
    OS-lock-free design (closing it needs ``fcntl``/``msvcrt``). The
    dead-pid staleness check (``_owner_pid_dead``) reaches this path sooner
    after a crash, but a corpus build is normally run one-at-a-time.
    """
    corpus.root.mkdir(parents=True, exist_ok=True)
    path = _lock_path(corpus)
    record = _build_record(owner, ttl_seconds, corpus.root)
    payload = json.dumps(record).encode("utf-8")

    if _atomic_create(path, payload):
        return

    existing = read_lock(corpus)
    if existing and not _is_stale(existing) and not force:
        raise CorpusLockHeldError(
            existing.get("owner", "unknown"),
            existing.get("acquired_at", ""),
            path,
        )

    _atomic_replace(path, payload)
    actual = read_lock(corpus) or {}
    if actual.get("owner") != owner or actual.get("pid") != os.getpid():
        raise CorpusLockHeldError(
            actual.get("owner", "unknown"),
            actual.get("acquired_at", ""),
            path,
        )


def release_lock(corpus: Corpus, *, owner: str | None = None) -> None:
    """Remove the lock file iff we still own it.

    When ``owner`` is given, the lock is removed only if the on-disk
    record names that owner AND our pid. Prevents a finally-block from
    clobbering a lock that another process reclaimed after our TTL
    expired. ``owner=None`` removes unconditionally for admin paths.
    """
    path = _lock_path(corpus)
    if not path.exists():
        return
    if owner is None:
        path.unlink()
        return
    record = read_lock(corpus) or {}
    if record.get("owner") == owner and record.get("pid") == os.getpid():
        path.unlink()


@contextmanager
def corpus_lock(corpus: Corpus, owner: str, *, ttl_seconds: int = 86400):
    """Hold the corpus lock for the duration of the block."""
    acquire_lock(corpus, owner=owner, ttl_seconds=ttl_seconds)
    try:
        yield
    finally:
        release_lock(corpus, owner=owner)
