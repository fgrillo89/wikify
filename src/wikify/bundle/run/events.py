"""``run/events.jsonl`` — append-only structured event ledger.

One JSON object per line. Carries every event the run emits: model
calls (with cost), stage changes, concept lifecycle, page commits,
inbox traffic, validation outcomes, run lifecycle. Cost is computed
on demand by filtering ``type == "call"``.

Schema is defined by :class:`Event` (Pydantic, ``extra="allow"`` for the
``data`` map). The allowed event-type vocabulary is the literal union
:data:`EventType`; appending an unknown type raises ``ValidationError``
rather than silently writing garbage that downstream tools cannot parse.

See ``docs/filesystem-state-design.md`` (Event log telemetry) for the
canonical envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ...api import Bundle

SCHEMA_VERSION = 1

EventType = Literal[
    "stage_changed",
    "cli_invoked",
    "concept_created",
    "concept_status_changed",
    "chunk_read",
    "evidence_added",
    "inbox_suggestion_created",
    "inbox_consolidated",
    "query_started",
    "wiki_page_read",
    "query_feedback_created",
    "draft_created",
    "call",
    "validation_completed",
    "page_committed",
    "page_refined",
    "budget_exceeded",
    "run_closed",
    "round_started",
    "round_completed",
    "dossier_promoted",
    "dossier_stalled",
    "dossier_parked",
    "pattern_dispatched",
    "corpus_drift_detected",
    "page_embedding_failed",
    "data_page_collision_skipped",
]


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_event_id() -> str:
    return uuid4().hex


class Event(BaseModel):
    """A single line in ``run/events.jsonl``.

    Required fields are envelope-level; optional indexing fields make
    grep / filter cheap; ``data`` is a free-form per-type payload.
    """

    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(default_factory=_new_event_id)
    run_id: str
    type: EventType
    at: str = Field(default_factory=_utcnow)
    actor: str
    data: dict[str, Any] = Field(default_factory=dict)

    # Optional top-level indexing fields (keep them on the envelope so
    # filters do not have to descend into ``data``).
    concept_id: str | None = None
    page_id: str | None = None
    chunk_id: str | None = None
    doc_id: str | None = None
    stage: str | None = None

    model_config = {"extra": "forbid"}


def append_event(bundle: Bundle, event: Event) -> None:
    """Append one event to ``<bundle>/run/events.jsonl``.

    The append is byte-level: a single ``open(..., "a")`` then
    ``write(json + "\\n") + flush``. This is atomic enough for one
    process; concurrent writers across processes should serialise
    through the bundle lock (see ``lock.py``).
    """
    bundle.run_dir.mkdir(parents=True, exist_ok=True)
    line = event.model_dump_json() + "\n"
    with bundle.events_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_events(bundle: Bundle) -> list[Event]:
    """Read every event in ``<bundle>/run/events.jsonl``.

    Returns an empty list if the file does not exist. Lines that fail
    to parse are skipped silently (callers that want strictness should
    re-validate; this helper is for cost / replay tools that tolerate
    a corrupted tail).
    """
    if not bundle.events_path.exists():
        return []
    out: list[Event] = []
    with bundle.events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Event.model_validate_json(line))
            except Exception:
                continue
    return out


def iter_events(bundle: Bundle):
    """Yield events one at a time from ``<bundle>/run/events.jsonl``.

    Same loose-parsing contract as :func:`read_events`. Useful for
    streaming over a large ledger without buffering the whole list.
    """
    if not bundle.events_path.exists():
        return
    with bundle.events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield Event.model_validate_json(line)
            except Exception:
                continue
