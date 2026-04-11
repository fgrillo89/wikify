"""Shared mutation contracts for the wiki-first runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WikiPagePatch:
    """One visible page creation or update within a run."""

    slug: str
    title: str
    page_type: str
    action: str  # create | update | promote | reconcile
    path: str = ""
    domains: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    status: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TelemetryEvent:
    """A structured telemetry event attached to a wiki run."""

    event_type: str
    stage: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WikiUpdateBundle:
    """Canonical mutation envelope for wiki-visible and operational updates."""

    run_id: str
    workflow_type: str
    status: str = "pending"
    page_patches: list[WikiPagePatch] = field(default_factory=list)
    link_updates: list[dict[str, Any]] = field(default_factory=list)
    source_note_updates: list[dict[str, Any]] = field(default_factory=list)
    provenance_updates: list[dict[str, Any]] = field(default_factory=list)
    concept_occurrences: list[dict[str, Any]] = field(default_factory=list)
    relation_evidence: list[dict[str, Any]] = field(default_factory=list)
    domain_updates: list[dict[str, Any]] = field(default_factory=list)
    maintenance_findings: list[dict[str, Any]] = field(default_factory=list)
    log_entries: list[str] = field(default_factory=list)
    telemetry_events: list[TelemetryEvent] = field(default_factory=list)

    def add_page_patch(self, patch: WikiPagePatch) -> None:
        self.page_patches.append(patch)

    def add_telemetry(self, event_type: str, *, stage: str = "", **payload: Any) -> None:
        self.telemetry_events.append(
            TelemetryEvent(
                event_type=event_type,
                stage=stage,
                payload=payload,
            )
        )
