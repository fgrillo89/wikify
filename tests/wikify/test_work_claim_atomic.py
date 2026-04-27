"""Tests for wikify.bundle.work.claim — per-concept claim files with TTL."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wikify.api import Bundle
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.claim import (
    ClaimHeldError,
    acquire_claim,
    claim_path,
    expire_stale_claims,
    list_claims,
    read_claim,
    release_claim,
)


def _bundle_with_concept(tmp_path: Path, slug: str = "ald") -> tuple[Bundle, str]:
    (tmp_path / "run").mkdir(parents=True)
    bundle = Bundle(root=tmp_path)
    s, _ = create_concept(bundle, page_id="ALD", slug=slug)
    return bundle, s


def test_acquire_writes_claim_file(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a", ttl_seconds=300)
    record = read_claim(bundle, slug)
    assert record is not None
    assert record["owner"] == "a"
    assert record["slug"] == slug
    assert record["ttl_seconds"] == 300


def test_release_owner_succeeds(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a")
    assert release_claim(bundle, slug, owner="a") is True
    assert read_claim(bundle, slug) is None


def test_release_non_owner_rejected(tmp_path: Path) -> None:
    """Release by a non-owner returns False; CLI maps this to exit 2."""
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a", ttl_seconds=600)
    assert release_claim(bundle, slug, owner="b") is False
    # Claim still in place.
    assert read_claim(bundle, slug) is not None


def test_contention_raises_claim_held(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a", ttl_seconds=300)
    with pytest.raises(ClaimHeldError) as exc:
        acquire_claim(bundle, slug, owner="b", ttl_seconds=300)
    assert exc.value.slug == slug
    assert exc.value.owner == "a"


def test_same_owner_re_acquire_refreshes_ttl(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a", ttl_seconds=10)
    acquire_claim(bundle, slug, owner="a", ttl_seconds=600)  # no error
    record = read_claim(bundle, slug)
    assert record is not None
    assert record["ttl_seconds"] == 600


def test_stale_claim_silently_reclaimed(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    expired_iso = (
        datetime.now(UTC) - timedelta(seconds=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim_path(bundle, slug).write_text(
        json.dumps(
            {
                "owner": "ghost",
                "acquired_at": expired_iso,
                "expires_at": expired_iso,
                "ttl_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    acquire_claim(bundle, slug, owner="b", ttl_seconds=600)
    assert read_claim(bundle, slug)["owner"] == "b"


def test_force_overrides_held_claim(tmp_path: Path) -> None:
    bundle, slug = _bundle_with_concept(tmp_path)
    acquire_claim(bundle, slug, owner="a")
    displaced = acquire_claim(bundle, slug, owner="b", force=True)
    assert displaced is not None
    assert displaced["owner"] == "a"


def test_list_claims_returns_active(tmp_path: Path) -> None:
    bundle, slug1 = _bundle_with_concept(tmp_path, slug="ald")
    create_concept(bundle, page_id="CVD", slug="cvd")
    acquire_claim(bundle, "ald", owner="a")
    acquire_claim(bundle, "cvd", owner="b")
    claims = list_claims(bundle)
    slugs = sorted(c["slug"] for c in claims)
    assert slugs == ["ald", "cvd"]


def test_expire_stale_claims_removes_only_expired(tmp_path: Path) -> None:
    bundle, slug1 = _bundle_with_concept(tmp_path, slug="ald")
    create_concept(bundle, page_id="CVD", slug="cvd")
    # Active live claim
    acquire_claim(bundle, "ald", owner="a", ttl_seconds=600)
    # Hand-write a stale claim
    expired_iso = (
        datetime.now(UTC) - timedelta(seconds=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim_path(bundle, "cvd").write_text(
        json.dumps(
            {
                "owner": "ghost",
                "acquired_at": expired_iso,
                "expires_at": expired_iso,
                "ttl_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    n = expire_stale_claims(bundle)
    assert n == 1
    assert read_claim(bundle, "cvd") is None
    assert read_claim(bundle, "ald") is not None
