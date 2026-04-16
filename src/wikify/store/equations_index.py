"""Per-corpus equation index -- the model-facing surface for equation lookup.

Built once at ingest time from ``Document.equations`` across all papers.
Equations with identical normalized LaTeX are merged into one record
with multiple ``source_doc_ids``.

Shape::

    {
      "version": 1,
      "records": [
        {
          "id":               "abc123def456",
          "latex":            "R = V/I",
          "normalized_latex": "r = v/i",
          "kind":             "unicode",
          "label":            "Ohm's law",
          "context":          "The resistance is...",
          "source_doc_ids":   ["doc_A", "doc_B"],
          "chunk_ids":        ["doc_A__c0005__x", "doc_B__c0012__y"]
        },
        ...
      ]
    }

Lookup surfaces:

- ``EquationIndex.for_doc(doc_id)`` -- equations from one paper.
- ``EquationIndex.by_kind(kind)`` -- filter by type.
- ``EquationIndex.search_latex(substring)`` -- substring match on LaTeX.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Chunk, Document


@dataclass(frozen=True)
class EquationRecord:
    id: str
    latex: str
    normalized_latex: str
    kind: str  # display, inline, chemical, named, unicode, image
    label: str
    context: str
    source_doc_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]


def _normalize_latex(latex: str) -> str:
    """Whitespace-normalize and lowercase for deduplication."""
    return " ".join(latex.split()).lower()


@dataclass
class EquationIndex:
    """Loaded view of ``corpus/equations.json``."""

    records: list[EquationRecord] = field(default_factory=list)
    _by_doc: dict[str, list[EquationRecord]] = field(default_factory=dict)
    _by_norm: dict[str, EquationRecord] = field(default_factory=dict)

    def for_doc(self, doc_id: str) -> list[EquationRecord]:
        return list(self._by_doc.get(doc_id, []))

    def by_kind(self, kind: str) -> list[EquationRecord]:
        return [r for r in self.records if r.kind == kind]

    def find_exact(self, normalized_latex: str) -> EquationRecord | None:
        """Exact lookup by normalized LaTeX. Use for provenance annotation."""
        return self._by_norm.get(normalized_latex)

    def search_latex(self, substring: str) -> list[EquationRecord]:
        """Substring match on normalized LaTeX. Use for exploration queries."""
        needle = _normalize_latex(substring)
        return [r for r in self.records if needle in r.normalized_latex]

    @classmethod
    def load(cls, path: Path) -> "EquationIndex":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        records = []
        for r in data.get("records", []):
            records.append(EquationRecord(
                id=r["id"],
                latex=r["latex"],
                normalized_latex=r.get("normalized_latex", _normalize_latex(r["latex"])),
                kind=r.get("kind", ""),
                label=r.get("label", ""),
                context=r.get("context", ""),
                source_doc_ids=tuple(r.get("source_doc_ids", [])),
                chunk_ids=tuple(r.get("chunk_ids", [])),
            ))
        idx = cls(records=records)
        idx._rebuild_by_doc()
        return idx

    def _rebuild_by_doc(self) -> None:
        self._by_doc.clear()
        self._by_norm.clear()
        for rec in self.records:
            for did in rec.source_doc_ids:
                self._by_doc.setdefault(did, []).append(rec)
            self._by_norm[rec.normalized_latex] = rec


def build_equations_index(
    docs: list[Document],
    chunks: list[Chunk],
) -> EquationIndex:
    """Build the equation index from all documents.

    Equations with identical normalized LaTeX are merged into one record
    with combined source_doc_ids and chunk_ids.
    """
    # Build chunk_id -> equation_ids mapping
    eq_to_chunks: dict[str, list[str]] = {}
    for ck in chunks:
        for eq_id in ck.equation_ids:
            eq_to_chunks.setdefault(eq_id, []).append(ck.id)

    # Collect all equations, merge by normalized latex
    merged: dict[str, dict] = {}  # normalized_latex -> merged record
    for doc in docs:
        for eq in doc.equations:
            latex = eq.get("latex", "")
            if not latex:
                continue
            norm = _normalize_latex(latex)
            eq_id = eq.get("id", "")
            if norm in merged:
                m = merged[norm]
                m["source_doc_ids"].add(doc.id)
                m["chunk_ids"].update(eq_to_chunks.get(eq_id, []))
                # Keep the more informative label/context
                if not m["label"] and eq.get("label"):
                    m["label"] = eq["label"]
                if len(eq.get("context", "")) > len(m["context"]):
                    m["context"] = eq["context"]
            else:
                merged[norm] = {
                    "id": eq_id,
                    "latex": latex,
                    "normalized_latex": norm,
                    "kind": eq.get("type", ""),
                    "label": eq.get("label", ""),
                    "context": eq.get("context", ""),
                    "source_doc_ids": {doc.id},
                    "chunk_ids": set(eq_to_chunks.get(eq_id, [])),
                }

    records = [
        EquationRecord(
            id=m["id"],
            latex=m["latex"],
            normalized_latex=m["normalized_latex"],
            kind=m["kind"],
            label=m["label"],
            context=m["context"],
            source_doc_ids=tuple(sorted(m["source_doc_ids"])),
            chunk_ids=tuple(sorted(m["chunk_ids"])),
        )
        for m in sorted(merged.values(), key=lambda m: m["normalized_latex"])
    ]
    idx = EquationIndex(records=records)
    idx._rebuild_by_doc()
    return idx


def save_equations_index(path: Path, idx: EquationIndex) -> Path:
    payload = {
        "version": 1,
        "records": [
            {
                "id": r.id,
                "latex": r.latex,
                "normalized_latex": r.normalized_latex,
                "kind": r.kind,
                "label": r.label,
                "context": r.context,
                "source_doc_ids": list(r.source_doc_ids),
                "chunk_ids": list(r.chunk_ids),
            }
            for r in idx.records
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".eqidx-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
