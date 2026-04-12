"""Closed vocabularies and protocol interfaces for wikify_simple."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .schema import (
        EditorBrief,
        ExtractRequest,
        ExtractResponse,
        OrchAction,
        OrchState,
        QueryRequest,
        QueryResponse,
        WriteRequest,
        WriteResponse,
    )


# --- enums ---------------------------------------------------------------


class ModelTier(str, Enum):
    SMALL = "S"
    MEDIUM = "M"
    LARGE = "L"


class Role(str, Enum):
    EXTRACTOR = "extractor"
    COMPACTOR = "compactor"
    EDITOR = "editor"
    WRITER = "writer"
    ORCHESTRATOR = "orchestrator"


class StrategyId(str, Enum):
    EXPLORE = "E"
    MIXED = "M"
    EXPLOIT = "X"


# --- protocols -----------------------------------------------------------


class Extractor(Protocol):
    # extract_many is a binding-level optimization, not part of this protocol.
    def extract(self, request: ExtractRequest) -> ExtractResponse: ...


class Compactor(Protocol):
    """Consolidates raw dossier entries into a deduplicated summary."""

    def compact(self, page_id: str, title: str, entries: list[dict]) -> dict: ...


class Editor(Protocol):
    """Reads compacted dossier material for a page and produces a brief."""

    def edit(
        self, page_id: str, title: str, dossier: list[dict], neighbors: list[dict]
    ) -> EditorBrief: ...


class Writer(Protocol):
    def write(self, request: WriteRequest) -> WriteResponse: ...


class Orchestrator(Protocol):
    def step(self, state: OrchState) -> OrchAction: ...


class Querier(Protocol):
    def answer(self, request: QueryRequest) -> QueryResponse: ...
