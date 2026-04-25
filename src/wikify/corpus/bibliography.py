"""Read structured corpus bibliography artifacts."""

import json
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
    """Load ``citations.json`` without repairing corpus artifacts."""
    if not corpus.citation_index_path.exists():
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in EMPTY_CITATION_INDEX.items()}
    return json.loads(corpus.citation_index_path.read_text(encoding="utf-8"))


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
