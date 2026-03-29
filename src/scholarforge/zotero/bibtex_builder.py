"""Build BibTeX entries from Paper models."""

from __future__ import annotations

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter

from scholarforge.store.models import Paper


def paper_to_bibtex(paper: Paper) -> str:
    """Build a minimal @article BibTeX entry from Paper fields.

    Entry ID format: ``{last_name_lower}{year}``  e.g. ``strukov2008``.
    """
    authors = paper.parsed_authors
    first_author_last = authors[0].split()[-1].lower() if authors else "unknown"
    year_str = str(paper.year) if paper.year else ""
    entry_id = f"{first_author_last}{year_str}"

    # BibTeX `author` field uses " and " as separator
    author_field = " and ".join(authors) if authors else ""

    entry: dict[str, str] = {
        "ENTRYTYPE": "article",
        "ID": entry_id,
        "title": paper.title or "",
        "author": author_field,
    }
    if year_str:
        entry["year"] = year_str
    if paper.doi:
        entry["doi"] = paper.doi

    db = BibDatabase()
    db.entries = [entry]

    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    return bibtexparser.dumps(db, writer)
