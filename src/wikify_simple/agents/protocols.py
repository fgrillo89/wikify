"""The three Protocols that strategies depend on.

A binding (fake or claude_code) implements all three. Strategies receive
the implementations by injection at the CLI level and never import any
binding module directly.
"""

from __future__ import annotations

from typing import Protocol

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


class Extractor(Protocol):
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
