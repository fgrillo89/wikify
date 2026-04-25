"""Tests for wikify.bundle.run.lifecycle — init_run + close_run."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Bundle
from wikify.bundle.run.events import read_events
from wikify.bundle.run.lifecycle import close_run, init_run
from wikify.bundle.run.state import load_state


def _empty(tmp_path: Path) -> Path:
    return tmp_path / "bundle"


def test_init_run_creates_state_and_first_event(tmp_path: Path) -> None:
    bundle_dir = _empty(tmp_path)
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle.open(bundle_dir)
    state = init_run(
        bundle,
        corpus_path="data/corpora/foo",
        strategy="baseline",
        target_haiku_eq=10000,
    )

    assert state.status == "active"
    assert state.run_id.startswith("run-")
    assert state.budget.target_haiku_eq == 10000

    loaded = load_state(bundle)
    assert loaded.run_id == state.run_id

    events = read_events(bundle)
    assert len(events) == 1
    assert events[0].type == "stage_changed"
    assert events[0].run_id == state.run_id


def test_init_run_creates_v2_layout(tmp_path: Path) -> None:
    bundle_dir = _empty(tmp_path)
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle.open(bundle_dir)
    init_run(bundle, corpus_path="x")
    for sub in ("run", "work", "work/inbox", "work/concepts", "wiki", "wiki/articles", "derived"):
        assert (bundle_dir / sub).is_dir(), f"missing: {sub}"


def test_close_run_completed(tmp_path: Path) -> None:
    bundle_dir = _empty(tmp_path)
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle.open(bundle_dir)
    init_run(bundle, corpus_path="x")
    closed = close_run(bundle, status="completed")

    assert closed.status == "completed"
    events = read_events(bundle)
    assert events[-1].type == "run_closed"
    assert events[-1].data["status"] == "completed"


def test_close_run_rejects_invalid_status(tmp_path: Path) -> None:
    bundle_dir = _empty(tmp_path)
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle.open(bundle_dir)
    init_run(bundle, corpus_path="x")
    with pytest.raises(ValueError, match="completed"):
        close_run(bundle, status="bogus")


def test_close_run_is_idempotent_on_status(tmp_path: Path) -> None:
    """Closing twice with the same status is allowed; both close events land."""
    bundle_dir = _empty(tmp_path)
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle.open(bundle_dir)
    init_run(bundle, corpus_path="x")
    close_run(bundle, status="completed")
    close_run(bundle, status="completed")
    closed_events = [e for e in read_events(bundle) if e.type == "run_closed"]
    assert len(closed_events) == 2
