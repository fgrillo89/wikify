"""Per-concept dossier: the accumulated knowledge about one wiki topic.

A dossier is the persistent, evolving artifact that grows as the extractor
reads more chunks. It is the editor's primary input for deciding whether
a concept has enough substance for a page and what to tell the writer.

Dossiers persist to disk at ``<bundle>/_dossiers/<page_id>.json`` so they
survive across incremental runs (``--feed``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..store.page_naming import url_slug

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NORM_RE.sub("-", s.lower()).strip("-")


@dataclass
class DossierEntry:
    """One piece of evidence about a concept from a single chunk."""

    chunk_id: str
    doc_id: str
    quote: str
    definition: str = ""
    summary: str = ""
    parameters: list[dict] = field(default_factory=list)
    mechanisms: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    equations: list[dict] = field(default_factory=list)
    section_type: str = ""
    figure_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "quote": self.quote,
            "definition": self.definition,
            "summary": self.summary,
            "parameters": self.parameters,
            "mechanisms": self.mechanisms,
            "relationships": self.relationships,
            "equations": self.equations,
            "section_type": self.section_type,
            "figure_ids": self.figure_ids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DossierEntry:
        return cls(
            chunk_id=d["chunk_id"],
            doc_id=d["doc_id"],
            quote=d.get("quote", ""),
            definition=d.get("definition", ""),
            summary=d.get("summary", ""),
            parameters=d.get("parameters", []),
            mechanisms=d.get("mechanisms", []),
            relationships=d.get("relationships", []),
            equations=d.get("equations", []),
            section_type=d.get("section_type", ""),
            figure_ids=d.get("figure_ids", []),
        )


@dataclass
class Dossier:
    """Accumulated knowledge about one concept.

    Grows incrementally as the extractor reads more chunks. Compacted
    periodically to keep the entry count bounded.
    """

    page_id: str
    title: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "concept"
    category: str | None = None

    entries: list[DossierEntry] = field(default_factory=list)

    # Compacted summary (populated by the compactor, not the extractor).
    # These represent the consolidated view after deduplication.
    canonical_definition: str = ""
    canonical_summary: str = ""
    merged_parameters: list[dict] = field(default_factory=list)
    merged_mechanisms: list[str] = field(default_factory=list)
    merged_relationships: list[dict] = field(default_factory=list)
    merged_equations: list[dict] = field(default_factory=list)

    # Metadata
    n_source_docs: int = 0
    n_compactions: int = 0

    @property
    def n_entries(self) -> int:
        return len(self.entries)

    @property
    def source_doc_ids(self) -> set[str]:
        return {e.doc_id for e in self.entries}

    @property
    def has_substance(self) -> bool:
        """Quick heuristic: enough material for a meaningful page?"""
        if self.n_entries < 2:
            return False
        if len(self.source_doc_ids) < 1:
            return False
        # Has at least one definition or summary
        has_def = bool(self.canonical_definition) or any(e.definition for e in self.entries)
        has_summary = bool(self.canonical_summary) or any(e.summary for e in self.entries)
        return has_def or has_summary

    def add_entry(self, entry: DossierEntry) -> None:
        """Add a new entry, avoiding exact duplicate chunk_ids."""
        if any(e.chunk_id == entry.chunk_id for e in self.entries):
            return
        self.entries.append(entry)
        self.n_source_docs = len(self.source_doc_ids)

    def apply_compaction(self, compacted: dict) -> None:
        """Apply compactor output: replace raw entries with ranked top evidence."""
        self.canonical_definition = compacted.get("definition", self.canonical_definition)
        self.canonical_summary = compacted.get("summary", self.canonical_summary)
        self.merged_parameters = compacted.get("parameters", self.merged_parameters)
        self.merged_mechanisms = compacted.get("mechanisms", self.merged_mechanisms)
        self.merged_relationships = compacted.get("relationships", self.merged_relationships)
        self.merged_equations = compacted.get("equations", self.merged_equations)

        # Replace entries with the top evidence selected by the compactor.
        top = compacted.get("top_evidence", [])
        if top:
            self.entries = [DossierEntry.from_dict(t) for t in top]
        self.n_compactions += 1

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "aliases": self.aliases,
            "kind": self.kind,
            "category": self.category,
            "entries": [e.to_dict() for e in self.entries],
            "canonical_definition": self.canonical_definition,
            "canonical_summary": self.canonical_summary,
            "merged_parameters": self.merged_parameters,
            "merged_mechanisms": self.merged_mechanisms,
            "merged_relationships": self.merged_relationships,
            "merged_equations": self.merged_equations,
            "n_source_docs": self.n_source_docs,
            "n_compactions": self.n_compactions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Dossier:
        dossier = cls(
            page_id=d["page_id"],
            title=d["title"],
            aliases=d.get("aliases", []),
            kind=d.get("kind", "concept"),
            category=d.get("category"),
        )
        dossier.entries = [DossierEntry.from_dict(e) for e in d.get("entries", [])]
        dossier.canonical_definition = d.get("canonical_definition", "")
        dossier.canonical_summary = d.get("canonical_summary", "")
        dossier.merged_parameters = d.get("merged_parameters", [])
        dossier.merged_mechanisms = d.get("merged_mechanisms", [])
        dossier.merged_relationships = d.get("merged_relationships", [])
        dossier.merged_equations = d.get("merged_equations", [])
        dossier.n_source_docs = d.get("n_source_docs", len(dossier.source_doc_ids))
        dossier.n_compactions = d.get("n_compactions", 0)
        return dossier

    def for_editor(self) -> dict:
        """Compact representation for the editor's prompt."""
        return {
            "page_id": self.page_id,
            "title": self.title,
            "aliases": self.aliases,
            "kind": self.kind,
            "category": self.category,
            "definition": self.canonical_definition or next(
                (e.definition for e in self.entries if e.definition), ""
            ),
            "summary": self.canonical_summary or next(
                (e.summary for e in self.entries if e.summary), ""
            ),
            "parameters": self.merged_parameters or [
                p for e in self.entries for p in e.parameters
            ][:10],
            "mechanisms": self.merged_mechanisms or list(dict.fromkeys(
                m for e in self.entries for m in e.mechanisms
            ))[:8],
            "relationships": self.merged_relationships or [
                r for e in self.entries for r in e.relationships
            ][:10],
            "equations": self.merged_equations or [
                eq for e in self.entries for eq in e.equations
            ][:10],
            "evidence": [
                {"chunk_id": e.chunk_id, "doc_id": e.doc_id, "quote": e.quote,
                 "section_type": e.section_type}
                for e in self.entries
            ],
            "n_sources": self.n_source_docs,
            "n_entries": self.n_entries,
        }


# --- persistence ---------------------------------------------------------


class DossierStore:
    """Read/write dossiers to ``<bundle>/_dossiers/``."""

    def __init__(self, bundle_root: Path) -> None:
        self._dir = bundle_root / "_dossiers"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, page_id: str) -> Path:
        return self._dir / f"{url_slug(page_id)}.json"

    def load(self, page_id: str) -> Dossier | None:
        p = self._path(page_id)
        if not p.exists():
            return None
        return Dossier.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, dossier: Dossier) -> None:
        p = self._path(dossier.page_id)
        p.write_text(json.dumps(dossier.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load_all(self) -> list[Dossier]:
        out = []
        for f in sorted(self._dir.glob("*.json")):
            out.append(Dossier.from_dict(json.loads(f.read_text(encoding="utf-8"))))
        return out

    def summary(self) -> dict:
        """Quick stats for the editor's overview."""
        dossiers = self.load_all()
        ready = [d for d in dossiers if d.has_substance]
        return {
            "total": len(dossiers),
            "ready_for_writing": len(ready),
            "total_entries": sum(d.n_entries for d in dossiers),
            "by_kind": {
                "concept": len([d for d in dossiers if d.kind == "concept"]),
                "person": len([d for d in dossiers if d.kind == "person"]),
            },
        }
