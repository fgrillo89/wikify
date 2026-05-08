"""Read structured corpus bibliography from the SQLite store."""

import json
import sqlite3
from typing import Any

from ..api import Corpus

EMPTY_CITATION_INDEX: dict[str, Any] = {
    "schema_version": 2,
    "entries": {},
    "doc_bibkeys": {},
    "doc_citations": {},
    "doi_bibkeys": {},
}


def load_citation_index(corpus: Corpus) -> dict[str, Any]:
    """Rebuild the legacy citation-index dict from `wikify.db`.

    The shape (``entries`` / ``doc_bibkeys`` / ``doc_citations`` /
    ``doi_bibkeys``) matches what the writer preload path and a couple
    of tests expect; the source of truth has moved to the
    ``bib_entries`` and ``documents`` tables.
    """
    if not corpus.sqlite_path.exists():
        return _empty()
    try:
        con = sqlite3.connect(corpus.sqlite_path)
        con.row_factory = sqlite3.Row
        try:
            doc_rows = con.execute(
                "SELECT doc_id, doi, title, year, container_title, "
                "publisher, authors_json, source_path "
                "FROM documents ORDER BY doc_id"
            ).fetchall()
            bib_rows = con.execute(
                "SELECT * FROM bib_entries ORDER BY doc_id, ord"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return _empty()

    index = _empty()
    entries: dict[str, dict[str, Any]] = index["entries"]
    doc_bibkeys: dict[str, str] = index["doc_bibkeys"]
    doc_citations: dict[str, list[str]] = index["doc_citations"]
    doi_bibkeys: dict[str, str] = index["doi_bibkeys"]

    # Source documents. The bibkey we expose is the doc_id itself —
    # bib-side rows use it as their target_doc_id and skill traversal
    # already handles ``doc:<doc_id>``.
    for r in doc_rows:
        doc_id = str(r["doc_id"])
        bibkey = doc_id
        doc_bibkeys[doc_id] = bibkey
        entries[bibkey] = {
            "bibkey": bibkey,
            "kind": "source",
            "title": str(r["title"] or ""),
            "year": str(r["year"] or "") if r["year"] is not None else "",
            "venue": str(r["container_title"] or ""),
            "publisher": str(r["publisher"] or ""),
            "doi": str(r["doi"] or ""),
            "authors": _safe_json_list(r["authors_json"]),
            "source_doc_id": doc_id,
        }
        if r["doi"]:
            doi_bibkeys[str(r["doi"]).lower()] = bibkey

    # Bib references. Group per-doc so doc_citations preserves the order
    # the bib_entries table already records via the ord column.
    per_doc: dict[str, list[str]] = {}
    for r in bib_rows:
        doc_id = str(r["doc_id"])
        local_key = r["local_key"] or r["bib_id"]
        bibkey = str(local_key)
        per_doc.setdefault(doc_id, []).append(bibkey)
        target = r["target_doc_id"]
        if target:
            # Reference resolved to a corpus paper -- alias to its source bibkey.
            entries.setdefault(bibkey, dict(entries.get(str(target), {})))
            entries[bibkey].setdefault("bibkey", bibkey)
            entries[bibkey]["kind"] = "reference"
            entries[bibkey]["source_doc_ids"] = (
                entries[bibkey].get("source_doc_ids") or []
            ) + [doc_id]
            continue
        kind = "reference" if r["title"] else "unresolved"
        entries[bibkey] = {
            "bibkey": bibkey,
            "kind": kind,
            "title": str(r["title"] or ""),
            "year": str(r["year"] or "") if r["year"] is not None else "",
            "venue": str(r["container_title"] or ""),
            "publisher": str(r["publisher"] or ""),
            "doi": str(r["doi"] or ""),
            "authors": _safe_json_list(r["authors_json"]),
            "raw_text": str(r["raw_text"] or ""),
            "resolution": str(r["resolution"] or ""),
            "source_doc_ids": [doc_id],
        }
        if r["doi"]:
            doi_bibkeys.setdefault(str(r["doi"]).lower(), bibkey)
    for doc_id, keys in per_doc.items():
        doc_citations[doc_id] = keys

    return index


def citation_context_for_docs(
    citation_index: dict[str, Any],
    doc_ids: set[str],
    *,
    cited_limit: int = 8,
) -> dict[str, Any]:
    """Return compact citation context for writer requests.

    This is a prompt payload, not an index. It includes only the source
    papers used as page evidence and a capped list of references cited by
    those papers.
    """
    entries: dict[str, dict[str, Any]] = citation_index.get("entries", {})
    doc_bibkeys: dict[str, str] = citation_index.get("doc_bibkeys", {})
    doc_citations: dict[str, list[str]] = citation_index.get("doc_citations", {})

    sources: dict[str, dict[str, str]] = {}
    cited_by_sources: dict[str, list[dict[str, str]]] = {}
    for doc_id in sorted(doc_ids):
        source_key = doc_bibkeys.get(doc_id, "")
        if source_key and source_key in entries:
            sources[doc_id] = _compact_entry(entries[source_key])

        refs: list[dict[str, str]] = []
        for ref_key in doc_citations.get(doc_id, [])[:cited_limit]:
            entry = entries.get(ref_key)
            if entry:
                refs.append(_compact_entry(entry))
        if refs:
            cited_by_sources[doc_id] = refs

    if not sources and not cited_by_sources:
        return {}
    return {
        "sources": sources,
        "cited_by_sources": cited_by_sources,
    }


def _compact_entry(entry: dict[str, Any]) -> dict[str, str]:
    out = {
        "bibkey": str(entry.get("bibkey", "")),
        "label": _label(entry),
        "title": str(entry.get("title", "")),
        "year": str(entry.get("year", "")),
        "venue": str(entry.get("venue", "")),
        "doi": str(entry.get("doi", "")),
        "raw_text": str(entry.get("raw_text", ""))[:500],
    }
    return {k: v for k, v in out.items() if v}


def _label(entry: dict[str, Any]) -> str:
    authors = entry.get("authors") or []
    first = ""
    if isinstance(authors, list) and authors:
        first = str(authors[0]).split(",")[0].strip()
    year = str(entry.get("year", "")).strip()
    title = str(entry.get("title", "")).strip()
    venue = str(entry.get("venue", "")).strip()
    head = f"{first} et al. ({year})" if first and year else first or year or title
    if venue and head:
        return f"{head}, {venue}"
    return head


def _empty() -> dict[str, Any]:
    return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in EMPTY_CITATION_INDEX.items()}


def _safe_json_list(raw) -> list:
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
