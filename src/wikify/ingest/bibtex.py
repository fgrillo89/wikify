"""Build BibTeX entries from wikify Documents.

Operates on the wikify Document model.
The corpus library is written to ``<corpus>/library.bib``.
"""

import re
from pathlib import Path

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter

from ..models import Document
from ..paths import CorpusPaths

_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


def _sanitize_id(s: str) -> str:
    s = _ID_SAFE_RE.sub("_", s).strip("_")
    return s or "unknown"


def paper_to_bibtex(doc: Document, citations: list[dict] | None = None) -> str:
    """Build a minimal ``@article`` BibTeX entry from a Document.

    The entry id is the sanitized ``doc.id``. ``citations`` is accepted
    for symmetry with the caller signature but is currently unused at
    the entry level (it is the corpus-side relation, not a per-entry
    field).
    """
    metadata = doc.metadata or {}
    authors_raw = metadata.get("authors") or []
    if isinstance(authors_raw, str):
        authors_list = [a.strip() for a in re.split(r"[,;]| and ", authors_raw) if a.strip()]
    else:
        authors_list = list(authors_raw)

    year = metadata.get("year")
    year_str = str(year) if year else ""
    doi = metadata.get("doi") or ""
    venue = metadata.get("venue") or metadata.get("journal") or ""

    entry: dict[str, str] = {
        "ENTRYTYPE": "article",
        "ID": _sanitize_id(doc.id),
        "title": doc.title or "",
        "author": " and ".join(authors_list),
    }
    if year_str:
        entry["year"] = year_str
    if doi:
        entry["doi"] = doi
    if venue:
        entry["journal"] = venue

    db = BibDatabase()
    db.entries = [entry]
    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    return bibtexparser.dumps(db, writer)


def write_corpus_bibtex(corpus: CorpusPaths, docs: list[Document]) -> Path:
    """Write ``<corpus>/library.bib`` containing one entry per Document."""
    corpus.ensure()
    bib_path = corpus.root / "library.bib"
    seen: dict[str, int] = {}
    entries: list[str] = []
    for doc in docs:
        bib = paper_to_bibtex(doc)
        m = re.search(r"@\w+\{([^,]+),", bib)
        entry_id = m.group(1) if m else ""
        if entry_id in seen:
            seen[entry_id] += 1
            suffix = chr(ord("a") + min(seen[entry_id] - 1, 25))
            bib = bib.replace(f"{{{entry_id},", f"{{{entry_id}{suffix},", 1)
        else:
            seen[entry_id] = 0
        entries.append(bib.strip())
    from ..store.corpus import atomic_write_text

    atomic_write_text(bib_path, "\n\n".join(entries) + "\n")
    return bib_path
