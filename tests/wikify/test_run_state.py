"""Tests for wikify.bundle.run.state — RunState schema + atomic IO."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify.api import Bundle
from wikify.bundle.run.state import (
    SCHEMA_VERSION,
    Budget,
    RunState,
    SchemaVersionMismatchError,
    load_state,
    save_state,
    touch,
)


def _bundle(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle(root=tmp_path)


def test_run_state_schema_version_is_one() -> None:
    state = RunState(run_id="r-1", corpus_path="data/corpora/x")
    assert state.schema_version == SCHEMA_VERSION == 1


def test_run_state_defaults() -> None:
    state = RunState(run_id="r-1", corpus_path="x")
    assert state.status == "active"
    # ``strategy`` defaults to empty string so the agent must explicitly
    # supply a label; Python never assumes a workflow.
    assert state.strategy == ""
    assert state.wiki_path == "wiki"
    assert state.work_path == "work"
    assert state.budget == Budget(target_haiku_eq=0, spent_haiku_eq=0)
    assert state.stages == {}
    assert state.created_at and state.updated_at


def test_run_state_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        RunState(run_id="r-1", corpus_path="x", unknown_field=1)


def test_run_state_invalid_status_rejected() -> None:
    with pytest.raises(ValidationError):
        RunState(run_id="r-1", corpus_path="x", status="bogus")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    state = RunState(
        run_id="r-1",
        corpus_path="data/corpora/x",
        budget=Budget(target_haiku_eq=1000, spent_haiku_eq=42),
        stages={"extract": "running"},
    )
    save_state(bundle, state)
    loaded = load_state(bundle)
    assert loaded == state


def test_save_state_is_atomic(tmp_path: Path) -> None:
    """The atomic write leaves no .state-tmp residue after a successful save."""
    bundle = _bundle(tmp_path)
    state = RunState(run_id="r-1", corpus_path="x")
    save_state(bundle, state)
    leftovers = [p for p in bundle.run_dir.iterdir() if p.name.startswith(".state-")]
    assert leftovers == []


def test_load_state_rejects_future_schema(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    bundle.state_path.write_text(
        '{"schema_version": 99, "run_id": "r-1", "corpus_path": "x"}',
        encoding="utf-8",
    )
    with pytest.raises(SchemaVersionMismatchError):
        load_state(bundle)


def test_touch_updates_timestamp(tmp_path: Path) -> None:
    state = RunState(run_id="r-1", corpus_path="x")
    bumped = touch(state)
    # The string compare is enough: touch always sets updated_at to now,
    # which is >= the original (same-second updates may be equal).
    assert bumped.updated_at >= state.updated_at
    assert bumped.created_at == state.created_at
