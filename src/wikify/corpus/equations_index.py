"""Per-corpus equation index loaded from the SQLite ``assets`` table.

Equations are projected into ``wikify.db`` at ingest time as
``asset_type='equation'`` rows; this module rebuilds the in-memory
deduped view (one record per normalised LaTeX) so the writer pipeline
can search/filter by equation content.

Lookup surfaces:

- ``EquationIndex.for_doc(doc_id)`` -- equations from one paper.
- ``EquationIndex.by_kind(kind)`` -- filter by type.
- ``EquationIndex.search_latex(substring)`` -- substring match on LaTeX.
"""

import json
import sqlite3
from dataclasses import dataclass, field

from ..api import Corpus


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
    """Loaded view of the corpus's equation assets."""

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
    def load(cls, corpus: Corpus) -> "EquationIndex":
        """Build the index from the corpus ``assets`` table."""
        if not corpus.sqlite_path.exists():
            return cls()
        con = sqlite3.connect(corpus.sqlite_path)
        con.row_factory = sqlite3.Row
        try:
            asset_rows = con.execute(
                "SELECT * FROM assets WHERE asset_type='equation' "
                "ORDER BY doc_id, ord",
            ).fetchall()
            chunk_rows = con.execute(
                "SELECT ca.asset_id, ca.chunk_id "
                "FROM chunk_assets ca "
                "JOIN assets a ON a.asset_id = ca.asset_id "
                "WHERE a.asset_type='equation' "
                "ORDER BY ca.asset_id, ca.chunk_id",
            ).fetchall()
        finally:
            con.close()

        chunks_by_asset: dict[str, list[str]] = {}
        for r in chunk_rows:
            chunks_by_asset.setdefault(str(r["asset_id"]), []).append(
                str(r["chunk_id"]),
            )

        merged: dict[str, dict] = {}
        for r in asset_rows:
            meta = _safe_json_obj(r["metadata_json"])
            latex = str(meta.get("latex") or r["content"] or "")
            if not latex:
                continue
            norm = _normalize_latex(latex)
            asset_id = str(r["asset_id"])
            doc_id = str(r["doc_id"])
            kind = str(meta.get("type") or "")
            label = str(r["caption"] or meta.get("label") or "")
            context = str(meta.get("context") or "")
            chunk_ids = chunks_by_asset.get(asset_id, [])
            if norm in merged:
                m = merged[norm]
                m["source_doc_ids"].add(doc_id)
                m["chunk_ids"].update(chunk_ids)
                if not m["label"] and label:
                    m["label"] = label
                if len(context) > len(m["context"]):
                    m["context"] = context
            else:
                merged[norm] = {
                    "id": asset_id,
                    "latex": latex,
                    "normalized_latex": norm,
                    "kind": kind,
                    "label": label,
                    "context": context,
                    "source_doc_ids": {doc_id},
                    "chunk_ids": set(chunk_ids),
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


def _safe_json_obj(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
