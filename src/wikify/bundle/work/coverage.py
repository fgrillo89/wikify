"""Chunk coverage report — how much of the corpus the bundle has touched.

Walks committed wiki pages (``wiki.db`` ``wiki_evidence`` table) and
in-flight notebooks (``work/concepts/*/notebook.md`` provenance), unions
their chunk_id sets, and divides by the corpus chunk count.

The loop's gap-explorer pattern shrinks the residual chunk set, but the
raw ratio cannot approach 1.0: structural chunks (references, captions,
figures, tables, acknowledgments, appendix, boilerplate) are never cited
as evidence and are ~half of a typical parsed-paper corpus. The report
therefore also exposes an ``addressable`` denominator that excludes
those kinds, which is the meaningful coverage signal for the workflow.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ...api import Bundle, Corpus
from ...corpus.handles import HandleIndex
from .chunk_ids import _build_suffix_index_from_rows, resolve_chunk_id
from .evidence import read_evidence
from .notebook import list_notebook_slugs, read_notebook

# Structural chunk kinds the explorer never cites (mirrors the explorer's
# ``excluded_kinds``). Coverage reports an ``addressable`` denominator that
# excludes these so the ratio reflects the body-text pool the loop can
# actually reach — references/captions/figures alone are ~half of a typical
# parsed-paper corpus, making a raw ratio near 1.0 structurally impossible.
EXCLUDED_SECTION_TYPES = frozenset(
    {
        "references",
        "acknowledgments",
        "appendix",
        "figure",
        "table",
        "caption",
        "boilerplate",
    }
)


@dataclass
class CoverageReport:
    n_covered: int = 0
    n_total: int = 0
    chunk_coverage_ratio: float = 0.0
    n_covered_committed: int = 0
    n_covered_in_flight: int = 0
    n_addressable: int = 0
    n_addressable_covered: int = 0
    addressable_coverage_ratio: float = 0.0
    per_doc: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n_covered": self.n_covered,
            "n_total": self.n_total,
            "chunk_coverage_ratio": self.chunk_coverage_ratio,
            "n_covered_committed": self.n_covered_committed,
            "n_covered_in_flight": self.n_covered_in_flight,
            "n_addressable": self.n_addressable,
            "n_addressable_covered": self.n_addressable_covered,
            "addressable_coverage_ratio": self.addressable_coverage_ratio,
            "per_doc": self.per_doc,
        }


def _corpus_chunk_index(
    corpus: Corpus,
) -> tuple[set[str], dict[str, str], set[str], frozenset[str], HandleIndex]:
    """Return ``(all_chunk_ids, chunk_id_to_doc_id, addressable_chunk_ids,
    canonical_ids, suffix_index)``.

    Reads directly from ``<corpus>/wikify.db``. Empty collections if the
    SQLite file is missing.  ``addressable_chunk_ids`` drops structural
    kinds (see :data:`EXCLUDED_SECTION_TYPES`) using the already-populated
    ``section_type`` / ``is_boilerplate`` columns — one extra column read,
    no recomputation. On a corpus that predates those columns, every chunk
    is treated as addressable (the ratio then equals the raw ratio).
    ``canonical_ids`` and ``suffix_index`` come from
    :func:`build_suffix_index` and resolve short handles in legacy ledgers.
    """
    if not corpus.sqlite_path.exists():
        return set(), {}, set(), frozenset(), HandleIndex()
    con = sqlite3.connect(str(corpus.sqlite_path))
    try:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(chunks)")}
        select = ["chunk_id", "doc_id"]
        if "section_type" in cols:
            select.append("section_type")
        if "is_boilerplate" in cols:
            select.append("is_boilerplate")
        rows = con.execute(f"SELECT {', '.join(select)} FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return set(), {}, set(), frozenset(), HandleIndex()
    finally:
        con.close()
    chunk_to_doc = {r["chunk_id"]: r["doc_id"] for r in rows}
    addressable: set[str] = set()
    for r in rows:
        keys = r.keys()
        section = ((r["section_type"] if "section_type" in keys else None) or "").lower()
        boiler = r["is_boilerplate"] if "is_boilerplate" in keys else 0
        if section in EXCLUDED_SECTION_TYPES or boiler:
            continue
        addressable.add(r["chunk_id"])
    canonical_ids, suffix_index = _build_suffix_index_from_rows(list(chunk_to_doc))
    return set(chunk_to_doc), chunk_to_doc, addressable, canonical_ids, suffix_index


def _normalize_chunk_id(
    raw: str,
    canonical_ids: frozenset[str],
    suffix_index: HandleIndex,
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
    suffix_index: HandleIndex,
    sqlite_path: Path | None = None,
) -> set[str]:
    """Resolve a set of possibly-short chunk ids to canonical ids."""
    out: set[str] = set()
    for cid in raw_ids:
        out.add(_normalize_chunk_id(cid, canonical_ids, suffix_index, sqlite_path))
    return out


def compute_coverage(bundle: Bundle, corpus: Corpus) -> CoverageReport:
    """Compute the bundle's chunk coverage against the corpus."""
    all_chunks, chunk_to_doc, addressable, canonical_ids, suffix_index = (
        _corpus_chunk_index(corpus)
    )
    if not all_chunks:
        return CoverageReport()
    sqlite_path = corpus.sqlite_path
    committed_raw = _committed_chunk_ids(bundle)
    in_flight_raw = _in_flight_chunk_ids(bundle)
    committed = _normalize_set(committed_raw, canonical_ids, suffix_index, sqlite_path) & all_chunks
    in_flight = _normalize_set(in_flight_raw, canonical_ids, suffix_index, sqlite_path) & all_chunks
    covered = committed | in_flight
    addressable_covered = covered & addressable

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
        n_addressable=len(addressable),
        n_addressable_covered=len(addressable_covered),
        addressable_coverage_ratio=(
            len(addressable_covered) / len(addressable) if addressable else 0.0
        ),
        per_doc=per_doc,
    )


def residual_chunk_ids(bundle: Bundle, corpus: Corpus) -> set[str]:
    """Chunks not yet in any committed page or in-flight notebook.

    P5 (gap-explorer) consumes this directly.
    """
    all_chunks, _, _, canonical_ids, suffix_index = _corpus_chunk_index(corpus)
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
