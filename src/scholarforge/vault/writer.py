"""Generate and update Obsidian vault markdown notes from ingested papers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scholarforge.config import settings
from scholarforge.store.models import Paper
from scholarforge.vault.templates import author_note, paper_note


def _sanitize_filename(name: str) -> str:
    """Remove characters invalid in filenames."""
    # Remove or replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")
    return name[:200]  # Cap length


def _paper_display_name(paper: Paper) -> str:
    """Create a display name like 'Vaswani 2017 - Attention Is All You Need'.

    Returns the sanitized form so it matches the filename and can be used
    directly in wikilinks like ``[[papers/Vaswani 2017 - Attention Is All You Need]]``.
    """
    authors = json.loads(paper.authors) if paper.authors else []
    first_author = authors[0].split()[-1] if authors else "Unknown"
    year = paper.year or "YYYY"
    title = paper.title or "Untitled"
    raw = f"{first_author} {year} - {title}"
    return _sanitize_filename(raw)


def vault_dir() -> Path:
    """Get the vault root directory (under data/, gitignored)."""
    vd = settings.data_dir / "vault"
    return vd


def ensure_vault_dirs() -> None:
    """Create vault subdirectories."""
    vd = vault_dir()
    for sub in [
        "papers",
        "authors",
        "topics",
    ]:
        (vd / sub).mkdir(parents=True, exist_ok=True)


def write_paper_note(
    paper: Paper,
    chunks_count: int,
    figures_count: int,
    topics: list[str] | None = None,
    similar_to: list[str] | None = None,
    cites_same: list[str] | None = None,
    figure_refs: list[tuple[str, str]] | None = None,
) -> Path:
    """Write a paper note to the vault. Returns the path of the written note."""
    ensure_vault_dirs()

    authors = json.loads(paper.authors) if paper.authors else []
    display_name = _paper_display_name(paper)
    safe_name = _sanitize_filename(display_name)

    note_content = paper_note(
        title=paper.title,
        authors=authors,
        year=paper.year,
        doi=paper.doi,
        abstract=paper.abstract,
        file_hash=paper.file_hash,
        source_path=paper.source_path,
        topics=topics,
        similar_to=similar_to,
        cites_same=cites_same,
        figure_refs=figure_refs,
        chunks_count=chunks_count,
        figures_count=figures_count,
    )

    note_path = vault_dir() / "papers" / f"{safe_name}.md"
    note_path.write_text(note_content, encoding="utf-8")

    # Also write author notes
    for author in authors:
        write_author_note(author, [display_name])

    return note_path


def write_author_note(name: str, papers: list[str]) -> Path:
    """Write or update an author note. Merges paper lists if note exists."""
    ensure_vault_dirs()
    safe_name = _sanitize_filename(name)
    note_path = vault_dir() / "authors" / f"{safe_name}.md"

    existing_papers: list[str] = []
    if note_path.exists():
        # Parse existing paper links
        content = note_path.read_text(encoding="utf-8")
        for line in content.split("\n"):
            m = re.match(r"- \[\[papers/(.+?)\]\]", line)
            if m:
                existing_papers.append(m.group(1))

    all_papers = list(dict.fromkeys(existing_papers + papers))  # Dedupe preserving order
    note_content = author_note(name, all_papers)
    note_path.write_text(note_content, encoding="utf-8")
    return note_path


def write_all_paper_notes(papers_with_counts: list[tuple[Paper, int, int]]) -> int:
    """Write vault notes for a batch of papers. Returns count written."""
    count = 0
    for paper, chunks_count, figures_count in papers_with_counts:
        write_paper_note(paper, chunks_count, figures_count)
        count += 1
    return count
