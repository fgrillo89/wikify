"""Bibliography entries, chunk citations, DOI re-resolution, BibTeX export.

Round-trip with `.citestore.db` is read-only: external lookups already
resolved into Crossref/OpenAlex JSON are copied into `bib_entries`
columns so the corpus is portable without the cache.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any, Literal

from .documents import _norm_doi

# Title-key tuning. The alnum form folds em-dash/hyphen, NFKD subscripts,
# Greek letters, PDF line-break artefacts (e.g. "HfO\n2"), and whitespace
# into one comparable string so the resolver is not defeated by surface
# punctuation. Two signals fire against unresolved bibs in the same year:
#
#   A. bib.title alnum prefix == doc.title alnum prefix (length _TITLE_PREFIX_LEN)
#   B. doc.title alnum prefix appears as substring of bib.raw_text alnum
#      (length _RAW_PREFIX_LEN; rescues the bibs whose title field is NULL)
#
# Both require year match. _MIN_DOC_TITLE_ALNUM gates the doc to avoid
# matching on stub titles ("Article", a chapter heading, etc.).
_TITLE_PREFIX_LEN = 50
_RAW_PREFIX_LEN = 40
_MIN_DOC_TITLE_ALNUM = 30


def _alnum(text: str | None) -> str:
    """NFKD-fold to ASCII, lowercase, strip non-alphanumerics."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def upsert_bib_entries(
    con: sqlite3.Connection,
    doc_id: str,
    entries: list[dict[str, Any]],
) -> None:
    """Replace a document's bibliography rows."""
    con.execute("DELETE FROM bib_entries WHERE doc_id = ?", (doc_id,))
    rows = []
    for ord_i, e in enumerate(entries or []):
        bib_id = e.get("bib_id") or f"{doc_id}::bib:{ord_i:04d}"
        rows.append({
            "bib_id": bib_id,
            "doc_id": doc_id,
            "ord": e.get("ord", ord_i),
            "local_key": e.get("local_key"),
            "raw_text": e.get("raw_text") or e.get("text"),
            "title": e.get("title"),
            "authors_json": json.dumps(e.get("authors") or []),
            "year": int(e["year"]) if e.get("year") else None,
            "container_title": e.get("container_title") or e.get("venue"),
            "publisher": e.get("publisher"),
            "doi": _norm_doi(e.get("doi")),
            "url": e.get("url"),
            "target_doc_id": e.get("target_doc_id"),
            "confidence": e.get("confidence"),
            "resolution": e.get("resolution"),
            "bib_json": json.dumps(e, default=str),
        })
    if not rows:
        return
    cols = ",".join(rows[0].keys())
    placeholders = ",".join(":" + k for k in rows[0].keys())
    con.executemany(
        f"INSERT OR REPLACE INTO bib_entries({cols}) VALUES ({placeholders})", rows,
    )


def get_bib_entries(con: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    return [
        dict(r) for r in con.execute(
            "SELECT * FROM bib_entries WHERE doc_id = ? ORDER BY ord", (doc_id,),
        )
    ]


def upsert_chunk_citations(
    con: sqlite3.Connection,
    doc_id: str,
    citations: list[dict[str, Any]],
) -> None:
    """Replace all chunk_citations rows belonging to *doc_id*."""
    con.execute("DELETE FROM chunk_citations WHERE doc_id = ?", (doc_id,))
    rows = []
    for c in citations or []:
        rows.append((
            c["chunk_id"], doc_id, c["bib_id"],
            c.get("marker_text", ""),
            c.get("char_start", 0),
            c.get("char_end", 0),
            c.get("context"),
        ))
    if not rows:
        return
    con.executemany(
        "INSERT OR IGNORE INTO chunk_citations(chunk_id, doc_id, bib_id, marker_text, "
        "char_start, char_end, context) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def reresolve_inbound(con: sqlite3.Connection, new_doc_id: str) -> int:
    """Resolve unresolved bib_entries to *new_doc_id* by DOI, alnum title,
    or alnum doc-title-prefix found in the bib raw_text. All non-DOI signals
    are year-gated.

    Returns the number of bib_entries newly resolved. Side effects:
    - Updates bib_entries.target_doc_id / resolution / confidence.
    - Inserts bib_entry -> document `resolved_to` edges.
    - Inserts document -> document `references` edges (one per source doc).
    """
    row = con.execute(
        "SELECT doc_id, doi, title, year FROM documents WHERE doc_id = ?",
        (new_doc_id,),
    ).fetchone()
    if not row:
        return 0
    doi = _norm_doi(row["doi"])
    year = row["year"]
    title_alnum = _alnum(row["title"])

    resolved: list[tuple[str, str, str, float]] = []  # (bib_id, src_doc_id, resolution, confidence)
    if doi:
        for r in con.execute(
            "SELECT bib_id, doc_id FROM bib_entries WHERE target_doc_id IS NULL AND LOWER(doi) = ?",
            (doi,),
        ):
            resolved.append((r["bib_id"], r["doc_id"], "exact_doi", 1.0))
    if year and len(title_alnum) >= _MIN_DOC_TITLE_ALNUM:
        title_key = title_alnum[:_TITLE_PREFIX_LEN]
        raw_prefix = title_alnum[:_RAW_PREFIX_LEN]
        seen_in_year: set[str] = set()
        for r in con.execute(
            "SELECT bib_id, doc_id, title, raw_text FROM bib_entries "
            "WHERE target_doc_id IS NULL AND year = ?",
            (year,),
        ):
            bib_id = r["bib_id"]
            if bib_id in seen_in_year:
                continue
            bib_title_alnum = _alnum(r["title"])
            if bib_title_alnum and bib_title_alnum[:_TITLE_PREFIX_LEN] == title_key:
                resolved.append((bib_id, r["doc_id"], "title_year", 0.85))
                seen_in_year.add(bib_id)
                continue
            bib_raw_alnum = _alnum(r["raw_text"])
            if bib_raw_alnum and raw_prefix in bib_raw_alnum:
                resolved.append((bib_id, r["doc_id"], "rawtext_year", 0.75))
                seen_in_year.add(bib_id)

    seen: set[str] = set()
    src_docs: set[str] = set()
    for bib_id, src_doc, resolution, confidence in resolved:
        if bib_id in seen:
            continue
        seen.add(bib_id)
        con.execute(
            "UPDATE bib_entries SET target_doc_id=?, resolution=?, confidence=? WHERE bib_id=?",
            (new_doc_id, resolution, confidence, bib_id),
        )
        con.execute(
            "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
            "VALUES ('bib_entry', ?, 'resolved_to', 'document', ?)",
            (bib_id, new_doc_id),
        )
        if src_doc != new_doc_id:
            src_docs.add(src_doc)
    for src_doc in src_docs:
        con.execute(
            "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
            "VALUES ('document', ?, 'references', 'document', ?)",
            (src_doc, new_doc_id),
        )
    return len(seen)


def upsert_reference_edges(con: sqlite3.Connection, doc_id: str) -> None:
    """Refresh outgoing reference edges for *doc_id*."""
    con.execute(
        "DELETE FROM graph_edges WHERE src_type='document' AND src_id=? AND kind='references'",
        (doc_id,),
    )
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'references', 'document', ?)",
        [
            (doc_id, r[0]) for r in con.execute(
                "SELECT DISTINCT target_doc_id FROM bib_entries "
                "WHERE doc_id = ? AND target_doc_id IS NOT NULL AND target_doc_id <> ?",
                (doc_id, doc_id),
            )
        ],
    )


def upsert_bib_resolved_edges(con: sqlite3.Connection, doc_id: str) -> None:
    """Refresh `bib_entry -> document resolved_to` edges for *doc_id*."""
    con.execute(
        "DELETE FROM graph_edges WHERE src_type='bib_entry' AND kind='resolved_to' "
        "AND src_id IN (SELECT bib_id FROM bib_entries WHERE doc_id = ?)",
        (doc_id,),
    )
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('bib_entry', ?, 'resolved_to', 'document', ?)",
        [
            (r[0], r[1]) for r in con.execute(
                "SELECT bib_id, target_doc_id FROM bib_entries "
                "WHERE doc_id = ? AND target_doc_id IS NOT NULL",
                (doc_id,),
            )
        ],
    )


def upsert_chunk_cites_edges(con: sqlite3.Connection, doc_id: str) -> None:
    """Refresh `chunk -> bib_entry cites` edges for *doc_id*."""
    con.execute(
        "DELETE FROM graph_edges WHERE src_type='chunk' AND kind='cites' "
        "AND src_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)",
        (doc_id,),
    )
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('chunk', ?, 'cites', 'bib_entry', ?)",
        [
            (r[0], r[1]) for r in con.execute(
                "SELECT DISTINCT chunk_id, bib_id FROM chunk_citations WHERE doc_id = ?",
                (doc_id,),
            )
        ],
    )


_BIBKEY_RE = re.compile(r"[^A-Za-z0-9_]+")


def _bibkey(year: Any, authors: list[Any] | None, fallback: str) -> str:
    first = ""
    if authors:
        first = str(authors[0]).split(",")[0].split()[-1] if authors[0] else ""
    yr = str(year) if year else "n.d."
    base = f"{first}{yr}" if first else fallback
    return _BIBKEY_RE.sub("", base) or fallback


def export_bibtex(con: sqlite3.Connection, kind: Literal["corpus", "cited"]) -> str:
    """Render BibTeX for either the corpus papers or cited works.

    `corpus` -> rows from `documents`.
    `cited`  -> rows from `bib_entries` (deduplicated by DOI when present, else by title+year).
    """
    out: list[str] = []
    seen: set[str] = set()
    if kind == "corpus":
        for r in con.execute(
            "SELECT doc_id, title, authors_json, year, container_title, publisher, doi, url "
            "FROM documents ORDER BY doc_id",
        ):
            authors = json.loads(r["authors_json"] or "[]")
            key = _bibkey(r["year"], authors, r["doc_id"])
            if key in seen:
                key = f"{key}_{r['doc_id'][:6]}"
            seen.add(key)
            fields = [
                ("title", r["title"]),
                ("author", " and ".join(str(a) for a in authors)),
                ("year", r["year"]),
                ("journal", r["container_title"]),
                ("publisher", r["publisher"]),
                ("doi", r["doi"]),
                ("url", r["url"]),
            ]
            out.append(_format_bib(key, fields))
    else:
        for r in con.execute(
            "SELECT bib_id, doi, title, authors_json, year, container_title, publisher, url "
            "FROM bib_entries ORDER BY bib_id",
        ):
            authors = json.loads(r["authors_json"] or "[]")
            dedup = (r["doi"] or f"{(r['title'] or '').lower()[:80]}|{r['year']}")
            if dedup in seen or not r["title"]:
                continue
            seen.add(dedup)
            key = _bibkey(r["year"], authors, r["bib_id"])
            fields = [
                ("title", r["title"]),
                ("author", " and ".join(str(a) for a in authors)),
                ("year", r["year"]),
                ("journal", r["container_title"]),
                ("publisher", r["publisher"]),
                ("doi", r["doi"]),
                ("url", r["url"]),
            ]
            out.append(_format_bib(key, fields))
    return "\n\n".join(out) + ("\n" if out else "")


def _format_bib(key: str, fields: list[tuple[str, Any]]) -> str:
    body_lines = [f"  {name} = {{{value}}}," for name, value in fields if value]
    if body_lines:
        body_lines[-1] = body_lines[-1].rstrip(",")
    return "@article{" + key + ",\n" + "\n".join(body_lines) + "\n}"


def import_citestore_facts(con: sqlite3.Connection, citestore_db: str) -> int:
    """Copy resolved Crossref/OpenAlex facts from .citestore.db into bib_entries.

    Read-only against the cache. Returns the number of bib rows enriched.
    """
    try:
        cache = sqlite3.connect(citestore_db)
        cache.row_factory = sqlite3.Row
    except sqlite3.Error:
        return 0
    enriched = 0
    try:
        for r in con.execute(
            "SELECT bib_id, doi FROM bib_entries WHERE doi IS NOT NULL AND title IS NULL",
        ):
            try:
                hit = cache.execute(
                    "SELECT title, authors_json, year, container_title, publisher "
                    "FROM citation_cache WHERE doi = ?",
                    (r["doi"],),
                ).fetchone()
            except sqlite3.Error:
                continue
            if not hit:
                continue
            con.execute(
                "UPDATE bib_entries SET title=?, authors_json=?, year=?, "
                "container_title=?, publisher=? WHERE bib_id=?",
                (
                    hit["title"], hit["authors_json"], hit["year"],
                    hit["container_title"], hit["publisher"], r["bib_id"],
                ),
            )
            enriched += 1
    finally:
        cache.close()
    return enriched
