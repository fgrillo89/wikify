"""PDF ingestion using pymupdf4llm."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import pymupdf4llm
from rich.console import Console

from scholarforge.extract.chunker import chunk_sections
from scholarforge.extract.citations import extract_citations
from scholarforge.extract.figure_refs import extract_figure_refs
from scholarforge.extract.figures import extract_figures
from scholarforge.extract.metadata import extract_metadata
from scholarforge.store.models import Chunk, Citation, Figure, FigureRef, Paper

console = Console()


@dataclass
class ParsedPaper:
    """Result of parsing a PDF, before persistence."""

    paper: Paper
    chunks: list[Chunk] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    figure_refs: list[FigureRef] = field(default_factory=list)
    md_text: str = ""
    skipped: bool = False


def _needs_ocr(md_text: str, doc: fitz.Document) -> bool:
    """Detect if pymupdf4llm output is mostly picture placeholders.

    Some PDFs (scanned, old formats) produce text via fitz.get_text() but
    pymupdf4llm's layout mode drops it in favor of image placeholders.
    If >50% of the markdown is placeholders and fitz can extract real text,
    we should re-extract with OCR.
    """
    import re

    placeholder_chars = sum(len(m.group()) for m in re.finditer(r"\*\*==>.*?<==\*\*", md_text))
    if len(md_text) == 0:
        return True

    placeholder_ratio = placeholder_chars / len(md_text)
    if placeholder_ratio < 0.3:
        return False

    # Check if fitz can extract meaningful text directly
    raw_text = ""
    for i in range(min(3, doc.page_count)):
        raw_text += doc[i].get_text()
    alphanumeric = sum(1 for c in raw_text if c.isalnum())
    return alphanumeric < 500  # True scanned — fitz can't extract either


def _fitz_fallback_markdown(doc: fitz.Document) -> str:
    """Build markdown from fitz raw text when pymupdf4llm layout mode fails.

    For old/scanned PDFs where fitz extracts text but pymupdf4llm doesn't.
    Joins fragmented lines, preserves paragraph breaks.
    """
    import re

    pages: list[str] = []
    for i in range(doc.page_count):
        raw = doc[i].get_text()
        # Rejoin hyphenated line breaks
        raw = re.sub(r"-\s*\n\s*", "", raw)
        # Collapse single newlines (fragmented lines) into spaces
        raw = re.sub(r"(?<!\n)\n(?!\n)", " ", raw)
        # Normalize multiple blank lines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        pages.append(raw.strip())

    return "\n\n".join(pages)


def parse_pdf(path: Path) -> ParsedPaper:
    """Parse a PDF into structured data. Does NOT touch the database or vault."""
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # First pass: standard layout extraction
    md_text = pymupdf4llm.to_markdown(str(path))

    doc = fitz.open(str(path))

    if _needs_ocr(md_text, doc):
        # True scanned PDF — run OCR
        console.print(f"[yellow]  Scanned PDF detected, running OCR:[/yellow] {path.name}")
        try:
            md_text = pymupdf4llm.to_markdown(str(path), force_ocr=True, ocr_language="eng")
        except Exception as e:
            console.print(f"[yellow]  OCR failed ({e}), using raw text fallback[/yellow]")
            md_text = _fitz_fallback_markdown(doc)
    else:
        import re

        placeholder_chars = sum(len(m.group()) for m in re.finditer(r"\*\*==>.*?<==\*\*", md_text))
        if len(md_text) > 0 and placeholder_chars / len(md_text) > 0.3:
            # pymupdf4llm failed but fitz has text — use fitz fallback
            console.print(f"[yellow]  Layout extraction poor, using raw text:[/yellow] {path.name}")
            md_text = _fitz_fallback_markdown(doc)

    # Extract metadata
    metadata = extract_metadata(doc, md_text, path.name)
    doc.close()

    # Build section tree
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

    # Chunk
    chunks = chunk_sections(md_text, section_tree, paper.id)

    # Figures
    figures = extract_figures(str(path), paper.id)

    # Citations from bibliography section
    citations = extract_citations(md_text, paper.id)

    # Caption-first figure references
    fig_refs = extract_figure_refs(md_text, paper.id)

    return ParsedPaper(
        paper=paper,
        chunks=chunks,
        figures=figures,
        citations=citations,
        figure_refs=fig_refs,
        md_text=md_text,
    )


def persist_parsed(parsed: ParsedPaper) -> None:
    """Persist a parsed paper to SQLite and vault."""
    from scholarforge.store.db import get_session
    from scholarforge.vault.writer import write_paper_note

    with get_session() as session:
        session.merge(parsed.paper)
        for chunk in parsed.chunks:
            session.merge(chunk)
        for figure in parsed.figures:
            session.merge(figure)
        for citation in parsed.citations:
            session.merge(citation)
        for fig_ref in parsed.figure_refs:
            session.merge(fig_ref)
        session.commit()

    write_paper_note(
        parsed.paper,
        len(parsed.chunks),
        len(parsed.figures),
        full_text=parsed.md_text,
    )


def ingest_pdf(path: Path, return_id: bool = False) -> int | str | None:
    """Ingest a single PDF into the knowledge base.

    Returns 1 on success / 0 on skip, or the paper ID string if return_id=True
    (None on skip).
    """
    file_bytes = path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    from scholarforge.store.db import get_session

    with get_session() as session:
        existing = session.get(Paper, file_hash)
        if existing and existing.file_hash == file_hash:
            console.print(f"[dim]Skipping (unchanged):[/dim] {path.name}")
            return None if return_id else 0

    parsed = parse_pdf(path)
    persist_parsed(parsed)

    console.print(
        f"[green]Ingested:[/green] {path.name} "
        f"({len(parsed.chunks)} chunks, {len(parsed.figures)} figures)"
    )
    return parsed.paper.id if return_id else 1


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

        while len(stack) > 1 and stack[-1].get("level", 0) >= level:
            stack.pop()

        stack[-1]["children"].append(node)
        stack.append(node)

    return tree
