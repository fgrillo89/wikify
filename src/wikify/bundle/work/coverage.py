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

from ...api import Bundle, Corpus
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


def _corpus_chunk_index(corpus: Corpus) -> tuple[set[str], dict[str, str]]:
    """Return ``(all_chunk_ids, chunk_id_to_doc_id)``.

    Reads directly from ``<corpus>/wikify.db``. Empty sets if the SQLite
    file is missing.
    """
    if not corpus.sqlite_path.exists():
        return set(), {}
    con = sqlite3.connect(str(corpus.sqlite_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT chunk_id, doc_id FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return set(), {}
    finally:
        con.close()
    chunk_to_doc = {r["chunk_id"]: r["doc_id"] for r in rows}
    return set(chunk_to_doc), chunk_to_doc


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


def compute_coverage(bundle: Bundle, corpus: Corpus) -> CoverageReport:
    """Compute the bundle's chunk coverage against the corpus."""
    all_chunks, chunk_to_doc = _corpus_chunk_index(corpus)
    if not all_chunks:
        return CoverageReport()
    committed = _committed_chunk_ids(bundle) & all_chunks
    in_flight = _in_flight_chunk_ids(bundle) & all_chunks
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
    all_chunks, _ = _corpus_chunk_index(corpus)
    if not all_chunks:
        return set()
    covered = (_committed_chunk_ids(bundle) | _in_flight_chunk_ids(bundle)) & all_chunks
    return all_chunks - covered
