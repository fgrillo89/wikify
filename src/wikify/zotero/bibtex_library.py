"""Maintain a corpus-wide .bib file that stays in sync with ingested papers.

The BibTeX library file is written to ``data/library.bib`` (or the
library-scoped equivalent) and is updated every time batch steps run.
It can be imported directly into Zotero, Mendeley, JabRef, or any other
reference manager that accepts BibTeX.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from wikify.core.store.models import Paper
from wikify.zotero.bibtex_builder import paper_to_bibtex

console = Console()


def rebuild_bibtex_library(papers: list[Paper], output_dir: Path) -> Path:
    """Write (or overwrite) the corpus BibTeX file from all papers.

    Returns the path to the written .bib file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    bib_path = output_dir / "library.bib"

    # Collect unique entry IDs to avoid duplicates from same-author/year combos
    seen_ids: dict[str, int] = {}
    entries: list[str] = []

    for paper in papers:
        bib_entry = paper_to_bibtex(paper)
        # Deduplicate entry IDs by appending a/b/c suffixes
        entry_id = _extract_entry_id(bib_entry)
        if entry_id in seen_ids:
            seen_ids[entry_id] += 1
            count = seen_ids[entry_id]
            if count <= 26:
                suffix = chr(ord("a") + count - 1)
            else:
                suffix = "a" + chr(ord("a") + count - 27)
            bib_entry = bib_entry.replace(f"{{{entry_id},", f"{{{entry_id}{suffix},", 1)
        else:
            seen_ids[entry_id] = 0
        entries.append(bib_entry.strip())

    bib_path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    console.print(f"[green]  BibTeX library: {len(entries)} entries -> {bib_path}[/green]")
    return bib_path


def _extract_entry_id(bib_text: str) -> str:
    """Extract the entry ID from a BibTeX string like '@article{strukov2008,'."""
    import re

    m = re.search(r"@\w+\{([^,]+),", bib_text)
    return m.group(1) if m else ""
