"""Per-concept claim files — the parallel-agent contention primitive.

Mirrors the bundle-wide ``run/lock`` shape but keyed per concept.
A worker that wants to mutate a concept folder must hold its claim;
suggestions can still be appended to the inbox without a claim
(they only mutate the inbox file, not the concept folder).

Atomicity: the fresh-acquisition path uses ``os.open(O_CREAT|O_EXCL)``
so two processes cannot both succeed. Stale reclaim and force-override
use ``os.replace`` and verify the on-disk owner after the replace.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...api import Bundle


class ClaimHeldError(RuntimeError):
    """Raised when ``acquire_claim`` finds a live claim held by a different owner."""

    def __init__(self, slug: str, owner: str, acquired_at: str) -> None:
        super().__init__(
            f"claim on {slug!r} held by {owner!r} since {acquired_at}"
        )
        self.slug = slug
        self.owner = owner
        self.acquired_at = acquired_at


def claim_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / ".claim"


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _is_stale(record: dict) -> bool:
    expires = record.get("expires_at")
    if expires:
        e = _parse_iso(expires)
        if e is None:
            return False
        return datetime.now(UTC) > e
    ttl = record.get("ttl_seconds")
    acquired = record.get("acquired_at")
    if not (ttl and acquired):
        return False
    a = _parse_iso(acquired)
    if a is None:
        return False
    return datetime.now(UTC) > a + timedelta(seconds=int(ttl))


def read_claim(bundle: Bundle, slug: str) -> dict | None:
    p = claim_path(bundle, slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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
    fd, tmp = tempfile.mkstemp(prefix=".claim-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise


def acquire_claim(
    bundle: Bundle,
    slug: str,
    *,
    owner: str,
    ttl_seconds: int = 1800,
    force: bool = False,
) -> dict | None:
    """Acquire the per-concept claim or raise :class:`ClaimHeldError`.

    Race-safe: the fresh-acquisition path uses ``O_EXCL``. Stale
    reclaim and force-override use ``os.replace`` and verify the
    on-disk owner. Same-owner re-acquisition refreshes the TTL.
    """
    p = claim_path(bundle, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl_seconds)
    record = {
        "owner": owner,
        "slug": slug,
        "pid": os.getpid(),
        "acquired_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_seconds": ttl_seconds,
    }
    payload = json.dumps(record).encode("utf-8")

    # Fast path: no existing claim.
    if _atomic_create(p, payload):
        return None

    # Slow path: file exists. Inspect liveness and ownership.
    existing = read_claim(bundle, slug)
    if existing and not _is_stale(existing):
        if existing.get("owner") == owner:
            # Same owner: refresh TTL via atomic replace.
            _atomic_replace(p, payload)
            return None
        if not force:
            raise ClaimHeldError(
                slug,
                existing.get("owner", "unknown"),
                existing.get("acquired_at", ""),
            )

    displaced = (
        existing
        if (existing and not _is_stale(existing) and force)
        else None
    )
    _atomic_replace(p, payload)
    actual = read_claim(bundle, slug) or {}
    if actual.get("owner") != owner or actual.get("pid") != os.getpid():
        raise ClaimHeldError(
            slug,
            actual.get("owner", "unknown"),
            actual.get("acquired_at", ""),
        )
    return displaced


def release_claim(bundle: Bundle, slug: str, *, owner: str) -> bool:
    """Release the claim if held by ``owner``. Returns True iff released.

    A non-owner caller is rejected (returns False) — the CLI maps this
    to ``EXIT_LOCK_HELD`` (exit code 2).
    """
    existing = read_claim(bundle, slug)
    if existing is None:
        return False
    if existing.get("owner") != owner and not _is_stale(existing):
        return False
    p = claim_path(bundle, slug)
    if p.exists():
        p.unlink()
    return True


def list_claims(bundle: Bundle) -> list[dict]:
    """Return every live claim record (with slug embedded)."""
    out: list[dict] = []
    if not bundle.work_concepts_dir.is_dir():
        return out
    for concept_dir in sorted(bundle.work_concepts_dir.iterdir()):
        if not concept_dir.is_dir():
            continue
        cp = concept_dir / ".claim"
        if not cp.is_file():
            continue
        try:
            record = json.loads(cp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record.setdefault("slug", concept_dir.name)
        out.append(record)
    return out


def expire_stale_claims(bundle: Bundle) -> int:
    """Delete every stale claim file. Returns count released."""
    n = 0
    for record in list_claims(bundle):
        if _is_stale(record):
            slug = record.get("slug")
            if slug:
                p = claim_path(bundle, slug)
                if p.exists():
                    p.unlink()
                    n += 1
    return n
