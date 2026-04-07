"""Extraction-note persistence.

Discovery emits inspectable ``ExtractionNote`` records that may be consumed
by a downstream consolidation pass or archived for experiment comparison.
This module provides an in-memory store and a JSONL file sink so notes can
be inspected without going through the canonical concept tables.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Protocol

from wikify.wiki.discovery.contracts import ExtractionNote


class NoteSink(Protocol):
    def write(self, note: ExtractionNote) -> None: ...
    def write_many(self, notes: Iterable[ExtractionNote]) -> None: ...


class InMemoryNoteStore:
    """Note store useful for tests and dry-run discovery passes."""

    def __init__(self) -> None:
        self._notes: list[ExtractionNote] = []

    def write(self, note: ExtractionNote) -> None:
        self._notes.append(note)

    def write_many(self, notes: Iterable[ExtractionNote]) -> None:
        for n in notes:
            self.write(n)

    def all(self) -> list[ExtractionNote]:
        return list(self._notes)

    def by_document(self, document_id: str) -> list[ExtractionNote]:
        return [n for n in self._notes if n.document_id == document_id]


class JsonlNoteSink:
    """Append extraction notes to a JSONL file for later inspection."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, note: ExtractionNote) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(note), default=str) + "\n")

    def write_many(self, notes: Iterable[ExtractionNote]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            for n in notes:
                f.write(json.dumps(asdict(n), default=str) + "\n")
