"""``run/lock`` — bundle-wide advisory lock with TTL.

Single global lock per run; per-concept claims live next to each
concept's work card. Stale locks (TTL expired) are silently reclaimed.
Live locks held by a different owner raise :class:`LockHeldError`,
which the CLI translates to ``EXIT_LOCK_HELD`` (exit code 2) via
``cli/_helpers.py``.

Atomicity: the fresh-acquisition path uses ``os.open(O_CREAT|O_EXCL)``
so two processes cannot both succeed. Stale reclaim uses a temp file +
``os.replace`` and re-reads the on-disk record after the replace to
detect a lost race; the loser raises ``LockHeldError``. This keeps the
implementation portable (POSIX + Windows) without a dependency on
``fcntl``/``msvcrt`` at the cost of an extra read on the stale-reclaim
path.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

    A killed run leaves its lock behind with the configured TTL; without
    this, the next run is blocked until the TTL elapses on a process that no
    longer exists. Only judged when the recorded ``host`` matches ours.
    Missing host/pid (older lock format) is 'cannot tell' -> not dead.

    PID reuse: when the lock carries ``started_at`` the owner is treated as
    dead unless the live pid's creation time still matches, so a recycled pid
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


def read_lock(bundle: Bundle) -> dict | None:
    """Return the lock record dict, or ``None`` if no lock file exists."""
    if not bundle.lock_path.exists():
        return None
    try:
        return json.loads(bundle.lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_record(owner: str, ttl_seconds: int) -> dict:
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
    }


def _atomic_create(path: Path, payload: bytes) -> bool:
    """Create *path* exclusively. Returns True if we created it."""
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
    """Replace *path* with *payload* via temp file + ``os.replace``."""
    fd, tmp = tempfile.mkstemp(prefix=".lock-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def acquire_lock(
    bundle: Bundle,
    owner: str,
    *,
    ttl_seconds: int = 3600,
    force: bool = False,
) -> dict | None:
    """Acquire ``<bundle>/run/lock`` or raise :class:`LockHeldError`.

    Race-safe: the fresh-acquisition path uses ``O_EXCL`` so two
    processes cannot both succeed. The stale-reclaim path uses
    ``os.replace`` and verifies the resulting on-disk owner; the
    loser raises ``LockHeldError``. ``force=True`` overrides a live
    lock and returns the displaced record.

    The stale-reclaim verify carries a narrow check->replace->readback
    window (accepted trade-off of the OS-lock-free design); the dead-pid
    staleness check reaches it sooner after a crash, but runs are normally
    one-at-a-time.
    """
    bundle.run_dir.mkdir(parents=True, exist_ok=True)
    record = _build_record(owner, ttl_seconds)
    payload = json.dumps(record).encode("utf-8")

    # Fast path: atomic create, no existing lock.
    if _atomic_create(bundle.lock_path, payload):
        return None

    # Slow path: file exists. Check liveness.
    existing = read_lock(bundle)
    if existing and not _is_stale(existing) and not force:
        raise LockHeldError(
            existing.get("owner", "unknown"),
            existing.get("acquired_at", ""),
        )

    # Either stale or force: atomic replace, then verify we ended up holding it.
    _atomic_replace(bundle.lock_path, payload)
    actual = read_lock(bundle) or {}
    if actual.get("owner") != owner or actual.get("pid") != os.getpid():
        raise LockHeldError(
            actual.get("owner", "unknown"),
            actual.get("acquired_at", ""),
        )
    return existing if (force and existing and not _is_stale(existing)) else None


def release_lock(bundle: Bundle, *, owner: str | None = None) -> None:
    """Remove the lock file if we still own it.

    When ``owner`` is given, the lock is only removed if the on-disk
    record still names that owner (and our pid). This prevents a
    finally-block from clobbering a lock that another process reclaimed
    after our TTL expired. When ``owner`` is ``None``, the lock is
    removed unconditionally — kept for ``wikify run lock --release``
    style admin paths that need to break a stuck lock.
    """
    if not bundle.lock_path.exists():
        return
    if owner is None:
        bundle.lock_path.unlink()
        return
    record = read_lock(bundle) or {}
    if record.get("owner") == owner and record.get("pid") == os.getpid():
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
        release_lock(bundle, owner=owner)
