"""Aggregations over ``run/events.jsonl`` for cost + workflow replay.

Reads the append-only event ledger (``cli_invoked``, ``call``,
``concept_created``, ``draft_created``, ``page_committed``,
``run_closed``, ...) and returns workflow-shaped rollups: per-type
and per-actor counts, a call cost rollup over ``type == "call"``,
concept lifecycle counts, and a ``run_closed`` summary. These power
the M5 hit-rate and the telemetry-parity gate.

The corpus knowledge graph keeps its own exploration trace inside
:mod:`wikify.corpus.graph`; that is independent of this ledger.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..api import Bundle
from ..bundle.run.events import Event, read_events


@dataclass
class TraceEntry:
    """One ledger event flattened for replay-shaped consumers.

    Keeps the small surface old call sites need (``actor``, ``method``,
    ``data``) but is now a thin view over :class:`Event`. Construct via
    :func:`load_trace` rather than directly.
    """

    timestamp: str
    actor: str
    method: str  # = Event.type
    data: dict = field(default_factory=dict)
    event_id: str = ""
    run_id: str = ""
    page_id: str | None = None
    concept_id: str | None = None
    chunk_id: str | None = None
    doc_id: str | None = None
    stage: str | None = None


def _entry_from_event(ev: Event) -> TraceEntry:
    return TraceEntry(
        timestamp=ev.at,
        actor=ev.actor,
        method=ev.type,
        data=dict(ev.data),
        event_id=ev.event_id,
        run_id=ev.run_id,
        page_id=ev.page_id,
        concept_id=ev.concept_id,
        chunk_id=ev.chunk_id,
        doc_id=ev.doc_id,
        stage=ev.stage,
    )


def load_trace(bundle: Bundle) -> list[TraceEntry]:
    """Load every event from ``<bundle>/run/events.jsonl`` as TraceEntries."""
    return [_entry_from_event(ev) for ev in read_events(bundle)]


def replay_stats(trace: Iterable[TraceEntry]) -> dict[str, Any]:
    """Aggregate the trace into the canonical rollup.

    Shape::

        {
          "total_events": int,
          "events_by_type":  {<type>: count, ...},
          "events_by_actor": {<actor>: count, ...},
          "calls": {
            "n_calls": int,
            "total_cost_haiku_eq": float,
            "total_cost_usd": float,
            "input_tokens": int,
            "output_tokens": int,
            "calls_by_stage": {<stage>: count, ...},
            "calls_by_model": {<model_id>: count, ...},
          },
          "concepts": {
            "created": int,
            "committed_pages": int,
            "distinct_concept_ids": int,
            "distinct_page_ids": int,
          },
          "run_closed": bool,
        }
    """
    entries = list(trace)
    by_type: Counter[str] = Counter(e.method for e in entries)
    by_actor: Counter[str] = Counter(e.actor for e in entries)

    n_calls = 0
    cost_haiku_eq = 0.0
    cost_usd = 0.0
    input_tokens = 0
    output_tokens = 0
    calls_by_stage: Counter[str] = Counter()
    calls_by_model: Counter[str] = Counter()

    distinct_concepts: set[str] = set()
    distinct_pages: set[str] = set()
    n_concepts_created = 0
    n_pages_committed = 0
    run_closed = False

    for e in entries:
        if e.method == "call":
            n_calls += 1
            cost_haiku_eq += float(e.data.get("cost_haiku_eq", 0.0) or 0.0)
            cost_usd += float(e.data.get("cost_usd", 0.0) or 0.0)
            input_tokens += int(e.data.get("input_tokens", 0) or 0)
            output_tokens += int(e.data.get("output_tokens", 0) or 0)
            stage = e.stage or e.data.get("stage") or "unknown"
            calls_by_stage[str(stage)] += 1
            model = e.data.get("model_id") or e.data.get("model") or "unknown"
            calls_by_model[str(model)] += 1
        elif e.method == "concept_created":
            n_concepts_created += 1
            if e.concept_id:
                distinct_concepts.add(e.concept_id)
        elif e.method == "page_committed":
            n_pages_committed += 1
            if e.page_id:
                distinct_pages.add(e.page_id)
        elif e.method == "run_closed":
            run_closed = True
        if e.concept_id:
            distinct_concepts.add(e.concept_id)
        if e.page_id:
            distinct_pages.add(e.page_id)

    return {
        "total_events": len(entries),
        "events_by_type": dict(by_type),
        "events_by_actor": dict(by_actor),
        "calls": {
            "n_calls": n_calls,
            "total_cost_haiku_eq": cost_haiku_eq,
            "total_cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "calls_by_stage": dict(calls_by_stage),
            "calls_by_model": dict(calls_by_model),
        },
        "concepts": {
            "created": n_concepts_created,
            "committed_pages": n_pages_committed,
            "distinct_concept_ids": len(distinct_concepts),
            "distinct_page_ids": len(distinct_pages),
        },
        "run_closed": run_closed,
    }


def exploration_timeline(trace: Iterable[TraceEntry]) -> list[dict[str, Any]]:
    """Chronological flattening of the trace, one row per event.

    Useful for replaying what the agent actually did vs what a skill
    expected — keeps the small set of fields callers commonly want
    without exposing the full :class:`Event` envelope.
    """
    out: list[dict[str, Any]] = []
    for i, e in enumerate(trace):
        out.append(
            {
                "step": i,
                "timestamp": e.timestamp,
                "type": e.method,
                "actor": e.actor,
                "concept_id": e.concept_id,
                "page_id": e.page_id,
                "stage": e.stage,
                "data": e.data,
            }
        )
    return out


def per_actor_breakdown(trace: Iterable[TraceEntry]) -> dict[str, dict[str, int]]:
    """Per-actor map of event-type -> count.

    A per-actor view: which event types each actor produced and how many.
    """
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for e in trace:
        out[e.actor][e.method] += 1
    return {actor: dict(counts) for actor, counts in out.items()}


__all__ = [
    "TraceEntry",
    "exploration_timeline",
    "load_trace",
    "per_actor_breakdown",
    "replay_stats",
]
