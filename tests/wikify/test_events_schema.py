"""Tests for wikify.bundle.run.events — Event envelope + append/iter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify.api import Bundle
from wikify.bundle.run.events import Event, append_event, iter_events, read_events


def _v2(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle.open(tmp_path)


def test_event_required_fields() -> None:
    e = Event(run_id="r-1", type="stage_changed", actor="cli")
    assert e.schema_version == 1
    assert e.run_id == "r-1"
    assert e.type == "stage_changed"
    assert e.actor == "cli"
    assert e.event_id  # auto-generated
    assert e.at  # auto-generated
    assert e.data == {}


def test_event_type_whitelist() -> None:
    """Unknown event types raise ValidationError, not a silent slip-through."""
    with pytest.raises(ValidationError):
        Event(run_id="r-1", type="not_a_real_event", actor="cli")


def test_event_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Event(run_id="r-1", type="cli_invoked", actor="cli", unknown_field=1)


def test_event_indexing_fields() -> None:
    e = Event(
        run_id="r-1",
        type="evidence_added",
        actor="consolidator",
        concept_id="Atomic Layer Deposition",
        chunk_id="doc1:003",
        doc_id="doc1",
        stage="write",
        data={"score": 0.91},
    )
    assert e.concept_id == "Atomic Layer Deposition"
    assert e.chunk_id == "doc1:003"
    assert e.doc_id == "doc1"
    assert e.stage == "write"


def test_append_then_read(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    e1 = Event(run_id="r-1", type="cli_invoked", actor="cli")
    e2 = Event(run_id="r-1", type="call", actor="writer-1", data={"haiku_eq": 100.0})
    append_event(bundle, e1)
    append_event(bundle, e2)

    events = read_events(bundle)
    assert len(events) == 2
    assert events[0].type == "cli_invoked"
    assert events[1].type == "call"
    assert events[1].data["haiku_eq"] == 100.0


def test_iter_events_streams(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    for i in range(5):
        append_event(
            bundle, Event(run_id="r-1", type="stage_changed", actor=f"a-{i}")
        )
    actors = [e.actor for e in iter_events(bundle)]
    assert actors == ["a-0", "a-1", "a-2", "a-3", "a-4"]


def test_iter_events_skips_corrupt_lines(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_event(bundle, Event(run_id="r-1", type="cli_invoked", actor="a"))
    with bundle.events_path.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
        fh.write("\n")  # blank line, also skipped
    append_event(bundle, Event(run_id="r-1", type="run_closed", actor="a"))
    types = [e.type for e in iter_events(bundle)]
    assert types == ["cli_invoked", "run_closed"]


def test_read_events_returns_empty_when_no_file(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    assert read_events(bundle) == []


def test_appended_events_are_jsonl(tmp_path: Path) -> None:
    """Each event is exactly one line; the file ends in a newline."""
    bundle = _v2(tmp_path)
    append_event(bundle, Event(run_id="r-1", type="cli_invoked", actor="a"))
    append_event(bundle, Event(run_id="r-1", type="stage_changed", actor="a"))
    text = bundle.events_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[-1] == ""  # trailing newline
    body_lines = [ln for ln in lines if ln]
    assert len(body_lines) == 2
    for ln in body_lines:
        json.loads(ln)  # valid JSON, no trailing comma etc.
