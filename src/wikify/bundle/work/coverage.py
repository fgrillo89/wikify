"""Chunk coverage report — how much of the corpus the bundle has touched.

Walks committed wiki pages (``wiki.db`` ``wiki_evidence`` table) and
in-flight notebooks (``work/concepts/*/notebook.md`` provenance), unions
their chunk_id sets, and divides by the corpus chunk count.

This is the primary objective of the ``wikify-investigate`` workflow.
Pushed to its limit, the loop's gap-explorer pattern shrinks the
residual chunk set; the ratio asymptotes toward 1.0 (less the corpus
noise floor).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ...api import Bundle, Corpus
from .chunk_ids import build_suffix_index, resolve_chunk_id
from .evidence import read_evidence
from .notebook import list_notebook_slugs, read_notebook


@dataclass
class CoverageReport:
    n_covered: int = 0
    n_total: int = 0
    chunk_coverage_ratio: float = 0.0
    n_covered_committed: int = 0
    n_covered_in_flight: int = 0
    per_doc: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n_covered": self.n_covered,
            "n_total": self.n_total,
            "chunk_coverage_ratio": self.chunk_coverage_ratio,
            "n_covered_committed": self.n_covered_committed,
            "n_covered_in_flight": self.n_covered_in_flight,
            "per_doc": self.per_doc,
        }


def _corpus_chunk_index(
    corpus: Corpus,
) -> tuple[set[str], dict[str, str], frozenset[str], dict[str, str]]:
    """Return ``(all_chunk_ids, chunk_id_to_doc_id, canonical_ids, suffix_index)``.

    Reads directly from ``<corpus>/wikify.db``. Empty collections if the
    SQLite file is missing.  ``canonical_ids`` and ``suffix_index`` come
    from :func:`build_suffix_index` and are used by the normalisation
    helpers to resolve short handles stored in legacy evidence ledgers.
    """
    if not corpus.sqlite_path.exists():
        return set(), {}, frozenset(), {}
    con = sqlite3.connect(str(corpus.sqlite_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT chunk_id, doc_id FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return set(), {}, frozenset(), {}
    finally:
        con.close()
    chunk_to_doc = {r["chunk_id"]: r["doc_id"] for r in rows}
    canonical_ids, suffix_index = build_suffix_index(corpus.sqlite_path)
    return set(chunk_to_doc), chunk_to_doc, canonical_ids, suffix_index


def _normalize_chunk_id(
    raw: str,
    canonical_ids: frozenset[str],
    suffix_index: dict[str, str],
    sqlite_path: Path | None = None,
) -> str:
    """Return the canonical id for *raw*, or *raw* itself if unresolvable.

    Accepts already-canonical ids, ``chunk:<hex>`` handles, and figure
    handles.  Falls back to *raw* so legacy handles that cannot be
    resolved are still counted (avoided empty coverage due to stale ids).
    """
    resolved = resolve_chunk_id(
        raw, suffix_index, canonical_ids,
        sqlite_path=sqlite_path,
    )
    return resolved if resolved is not None else raw


def _committed_chunk_ids(bundle: Bundle) -> set[str]:
    """Union of chunk_ids cited by every committed wiki page (wiki.db)."""
    p = bundle.sqlite_path
    if not p.exists():
        return set()
    con = sqlite3.connect(str(p))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT DISTINCT chunk_id FROM wiki_evidence WHERE chunk_id IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        con.close()
    return {r["chunk_id"] for r in rows if r["chunk_id"]}


def _in_flight_chunk_ids(bundle: Bundle) -> set[str]:
    """Union of ``covered_chunks`` from notebooks AND active evidence
    ledgers from every work-concept folder.

    Always unions both sources. Notebook provenance may lag the evidence
    ledger by one round (the editor folds explorer deltas in between
    Tasks), so taking the union keeps coverage monotonic regardless of
    where the ground truth currently sits. Also picks up pre-investigate
    bundles that have evidence ledgers but no notebooks at all.
    """
    out: set[str] = set()
    for slug in list_notebook_slugs(bundle):
        nb = read_notebook(bundle, slug)
        out.update(nb.front.provenance.covered_chunks)
    concepts_dir = bundle.work_concepts_dir
    if concepts_dir.is_dir():
        for entry in concepts_dir.iterdir():
            if not entry.is_dir():
                continue
            for r in read_evidence(bundle, entry.name):
                if r.status == "active" and r.chunk_id:
                    out.add(r.chunk_id)
    return out


def _normalize_set(
    raw_ids: set[str],
    canonical_ids: frozenset[str],
    suffix_index: dict[str, str],
    sqlite_path: Path | None = None,
) -> set[str]:
    """Resolve a set of possibly-short chunk ids to canonical ids."""
    out: set[str] = set()
    for cid in raw_ids:
        out.add(_normalize_chunk_id(cid, canonical_ids, suffix_index, sqlite_path))
    return out


def compute_coverage(bundle: Bundle, corpus: Corpus) -> CoverageReport:
    """Compute the bundle's chunk coverage against the corpus."""
    all_chunks, chunk_to_doc, canonical_ids, suffix_index = _corpus_chunk_index(corpus)
    if not all_chunks:
        return CoverageReport()
    sqlite_path = corpus.sqlite_path
    committed_raw = _committed_chunk_ids(bundle)
    in_flight_raw = _in_flight_chunk_ids(bundle)
    committed = _normalize_set(committed_raw, canonical_ids, suffix_index, sqlite_path) & all_chunks
    in_flight = _normalize_set(in_flight_raw, canonical_ids, suffix_index, sqlite_path) & all_chunks
    covered = committed | in_flight

    per_doc: dict[str, dict] = {}
    for chunk_id, doc_id in chunk_to_doc.items():
        d = per_doc.setdefault(doc_id, {"total": 0, "covered": 0})
        d["total"] += 1
        if chunk_id in covered:
            d["covered"] += 1
    for d in per_doc.values():
        d["ratio"] = d["covered"] / d["total"] if d["total"] else 0.0

    return CoverageReport(
        n_covered=len(covered),
        n_total=len(all_chunks),
        chunk_coverage_ratio=len(covered) / len(all_chunks),
        n_covered_committed=len(committed),
        n_covered_in_flight=len(in_flight),
        per_doc=per_doc,
    )


def residual_chunk_ids(bundle: Bundle, corpus: Corpus) -> set[str]:
    """Chunks not yet in any committed page or in-flight notebook.

    P5 (gap-explorer) consumes this directly.
    """
    all_chunks, _, canonical_ids, suffix_index = _corpus_chunk_index(corpus)
    if not all_chunks:
        return set()
    sqlite_path = corpus.sqlite_path
    committed = _normalize_set(
        _committed_chunk_ids(bundle), canonical_ids, suffix_index, sqlite_path
    )
    in_flight = _normalize_set(
        _in_flight_chunk_ids(bundle), canonical_ids, suffix_index, sqlite_path
    )
    covered = (committed | in_flight) & all_chunks
    return all_chunks - covered
