"""PDF ingestion using pymupdf4llm."""

import hashlib
import json
from pathlib import Path

import fitz
import pymupdf4llm
from rich.console import Console

from scholarforge.extract.chunker import chunk_sections
from scholarforge.extract.figures import extract_figures
from scholarforge.extract.metadata import extract_metadata
from scholarforge.store.db import get_session
from scholarforge.store.models import Paper

console = Console()


def ingest_pdf(path: Path) -> int:
    """Ingest a single PDF into the knowledge base. Returns 1 on success, 0 on skip."""
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Check if already ingested with same hash
    with get_session() as session:
        existing = session.get(Paper, file_hash)
        if existing and existing.file_hash == file_hash:
            console.print(f"[dim]Skipping (unchanged):[/dim] {path.name}")
            return 0

    # Extract structured markdown
    md_text = pymupdf4llm.to_markdown(str(path))

    # Extract metadata
    doc = fitz.open(str(path))
    metadata = extract_metadata(doc, md_text, path.name)
    doc.close()

    # Build section tree from markdown headings
    section_tree = _parse_section_tree(md_text)

    # Create paper record
    paper = Paper(
        id=file_hash,
        title=metadata.get("title", path.stem),
        authors=json.dumps(metadata.get("authors", [])),
        abstract=metadata.get("abstract"),
        year=metadata.get("year"),
        doi=metadata.get("doi"),
        source_path=str(path),
        file_hash=file_hash,
        section_tree=json.dumps(section_tree),
    )

    # Chunk the content
    chunks = chunk_sections(md_text, section_tree, paper.id)

    # Extract figures
    figures = extract_figures(str(path), paper.id)

    # Persist
    with get_session() as session:
        session.merge(paper)
        for chunk in chunks:
            session.merge(chunk)
        for figure in figures:
            session.merge(figure)
        session.commit()

    console.print(
        f"[green]Ingested:[/green] {path.name} "
        f"({len(chunks)} chunks, {len(figures)} figures)"
    )
    return 1


def _parse_section_tree(md_text: str) -> dict:
    """Parse markdown headings into a nested section tree."""
    tree: dict = {"title": "", "children": []}
    stack = [tree]

    for line in md_text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue

        level = 0
        for ch in stripped:
            if ch == "#":
                level += 1
            else:
                break

        heading = stripped[level:].strip()
        node = {"title": heading, "level": level, "children": []}

        # Find parent: pop stack until we find a node with lower level
        while len(stack) > 1 and stack[-1].get("level", 0) >= level:
            stack.pop()

        stack[-1]["children"].append(node)
        stack.append(node)

    return tree
