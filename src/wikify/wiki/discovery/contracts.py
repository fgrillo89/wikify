"""Typed contracts for the discovery subsystem.

Every intermediate artifact discovery produces or consumes is described here
as an explicit, serializable dataclass. Workflow nodes accept and return
these objects rather than reaching into shared mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UnitKind(str, Enum):
    """Kinds of addressable extraction units a workflow may operate on."""

    CHUNK = "chunk"
    SECTION = "section"
    SYNOPSIS = "synopsis"
    FIGURE = "figure"
    TABLE = "table"
    SLIDE = "slide"
    EQUATION = "equation"
    PAGE_IMAGE = "page_image"
    MIXED = "mixed"


class ModalityKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"
    EQUATION = "equation"


@dataclass(frozen=True)
class ArtifactRef:
    """Typed reference to one persisted artifact (or collection thereof).

    The DAG executor uses ``kind`` to type-check node bindings and ``key`` as
    the slot name in the run-scoped artifact store.
    """

    kind: str
    key: str
    cardinality: str = "one"  # "one" | "many"

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.kind}:{self.key}"


@dataclass
class DocumentProfile:
    """Profile of one source document used to choose a discovery strategy."""

    document_id: str
    document_type: str  # publication | slide_deck | html_note | markdown | mixed | unknown
    parser_confidence: float = 1.0
    structural_sections: list[str] = field(default_factory=list)
    modalities: list[ModalityKind] = field(default_factory=lambda: [ModalityKind.TEXT])
    token_budget_hint: int | None = None
    priority: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionUnit:
    """One addressable unit a model pass can interrogate."""

    unit_id: str
    document_id: str
    kind: UnitKind
    modality: ModalityKind = ModalityKind.TEXT
    payload: Any = None  # text, image bytes path, table rows, etc.
    parent_unit_id: str | None = None
    section: str | None = None
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionNote:
    """A model-produced note tied to one or more extraction units."""

    note_id: str
    document_id: str
    unit_ids: list[str]
    strategy_id: str
    node_id: str
    content: dict[str, Any]
    confidence: float = 0.0
    model: str | None = None
    created_at: float = 0.0


@dataclass
class CandidateConcept:
    """Pre-merge concept/entity hypothesis emitted by a discovery pass."""

    name: str
    kind: str  # technique | material | concept | entity | dataset | ...
    document_id: str
    unit_ids: list[str]
    note_ids: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoverageRecord:
    """What has been processed, skipped, deferred, or retried for one document."""

    document_id: str
    strategy_id: str
    processed_unit_ids: set[str] = field(default_factory=set)
    deferred_unit_ids: set[str] = field(default_factory=set)
    failed_unit_ids: set[str] = field(default_factory=set)
    epoch: int = 0
    last_touched: float = 0.0

    def mark_processed(self, unit_id: str) -> None:
        self.processed_unit_ids.add(unit_id)
        self.deferred_unit_ids.discard(unit_id)
        self.failed_unit_ids.discard(unit_id)

    def remaining(self, all_unit_ids: list[str]) -> list[str]:
        return [u for u in all_unit_ids if u not in self.processed_unit_ids]


@dataclass(frozen=True)
class DagNodeSpec:
    """One reusable step definition in a discovery workflow."""

    node_id: str
    impl: str  # registry key
    inputs: dict[str, ArtifactRef] = field(default_factory=dict)
    outputs: dict[str, ArtifactRef] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DagRunSpec:
    """One configured workflow instance ready for execution."""

    workflow_id: str
    nodes: tuple[DagNodeSpec, ...]
    strategy_id: str
    config_hash: str
    config_source: str  # path or "<inline>"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryStrategy:
    """A named strategy: ordered pass definition plus coverage policy."""

    strategy_id: str
    version: str
    document_types: tuple[str, ...]
    workflow_id: str
    synopsis_budget_chars: int = 3000
    chunk_budget: int = 64
    image_budget: int = 16
    multimodal_enabled: bool = True
    note_dump_enabled: bool = False
    model_tier: str = "fast"
    description: str = ""
