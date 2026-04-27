"""Telemetry parity gate — pins the ``run/events.jsonl`` rollup shape.

A single append-only ``run/events.jsonl`` ledger carries every event
the run emits. This test pins the aggregator output against a
checked-in golden fixture so a future change to
:mod:`wikify.eval.trace_replay` cannot silently drop the fields the
gates depend on (call cost, per-stage call counts, distinct
concepts/pages, run-closed flag).

The fixture is hand-crafted so it stays hermetic — no live run, no LLM,
no API key.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.run.lifecycle import init_run
from wikify.eval.trace_replay import load_trace, replay_stats

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "telemetry_parity"
_EVENTS_FIXTURE = _FIXTURE_DIR / "events.jsonl"
_EXPECTED_FIXTURE = _FIXTURE_DIR / "expected.json"


def _bundle_with_fixture_events(tmp_path: Path) -> Bundle:
    """Initialise a bundle and overwrite ``run/events.jsonl`` with the fixture."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "run").mkdir()
    bundle = Bundle(root=root)
    # ``init_run`` writes its own cli_invoked event line; we replace the
    # whole file with the fixture so the parity gate compares against
    # exactly the hand-authored ledger.
    init_run(bundle, corpus_path="data/corpora/foo")
    shutil.copy(_EVENTS_FIXTURE, bundle.events_path)
    return bundle


def test_events_load_into_trace_entries(tmp_path: Path) -> None:
    bundle = _bundle_with_fixture_events(tmp_path)
    trace = load_trace(bundle)
    # Sanity: the fixture has 14 events; loader must surface every one.
    assert len(trace) == 14
    # Sanity: the first row carries the ``cli_invoked`` envelope and the
    # actor/method/data fields the event vocabulary is shaped around.
    first = trace[0]
    assert first.method == "cli_invoked"
    assert first.actor == "cli"
    assert first.data["command"] == "run init"


def test_replay_stats_matches_golden_fixture(tmp_path: Path) -> None:
    bundle = _bundle_with_fixture_events(tmp_path)
    trace = load_trace(bundle)
    stats = replay_stats(trace)

    expected = json.loads(_EXPECTED_FIXTURE.read_text(encoding="utf-8"))

    # Compare totals and structured rollups directly. Floats compared
    # with a tight tolerance to absorb arithmetic precision noise.
    assert stats["total_events"] == expected["total_events"]
    assert stats["events_by_type"] == expected["events_by_type"]
    assert stats["events_by_actor"] == expected["events_by_actor"]
    assert stats["concepts"] == expected["concepts"]
    assert stats["run_closed"] == expected["run_closed"]

    calls = stats["calls"]
    expected_calls = expected["calls"]
    assert calls["n_calls"] == expected_calls["n_calls"]
    assert calls["input_tokens"] == expected_calls["input_tokens"]
    assert calls["output_tokens"] == expected_calls["output_tokens"]
    assert calls["calls_by_stage"] == expected_calls["calls_by_stage"]
    assert calls["calls_by_model"] == expected_calls["calls_by_model"]
    assert abs(calls["total_cost_usd"] - expected_calls["total_cost_usd"]) < 1e-9
    assert abs(calls["total_cost_haiku_eq"] - expected_calls["total_cost_haiku_eq"]) < 1e-9


def test_call_cost_rollup_uses_call_events_only(tmp_path: Path) -> None:
    """Cost is derived from ``type == 'call'`` events, not stage_changed/etc."""
    bundle = _bundle_with_fixture_events(tmp_path)
    trace = load_trace(bundle)
    stats = replay_stats(trace)

    # Hand-derived totals: 0.024 + 0.003 + 0.002 = 0.029 USD.
    assert abs(stats["calls"]["total_cost_usd"] - 0.029) < 1e-9
    # Every call event in the fixture carries a model_id.
    assert sum(stats["calls"]["calls_by_model"].values()) == stats["calls"]["n_calls"]


def test_replay_stats_on_empty_bundle_returns_zeros(tmp_path: Path) -> None:
    """A fresh bundle (no events written beyond init) must still aggregate cleanly."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "run").mkdir()
    bundle = Bundle(root=root)
    # No events file at all.
    stats = replay_stats(load_trace(bundle))
    assert stats["total_events"] == 0
    assert stats["calls"]["n_calls"] == 0
    assert stats["calls"]["total_cost_usd"] == 0.0
    assert stats["concepts"]["committed_pages"] == 0
    assert stats["run_closed"] is False
