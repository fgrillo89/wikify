"""Dispatcher: file extension -> parser."""

from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

console = Console()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx"}


def ingest_path(path: Path, parallel: bool = False, max_workers: int = 4) -> int:
    """Ingest a file or directory. Returns count of documents ingested."""
    if path.is_file():
        return _ingest_file(path)
    elif path.is_dir():
        files = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(sorted(path.rglob(f"*{ext}")))
        if not files:
            return 0

        if parallel and len(files) > 1:
            return _ingest_parallel(files, max_workers)
        else:
            count = 0
            for file in files:
                count += _ingest_file(file)
            return count
    return 0


def _ingest_parallel(files: list[Path], max_workers: int) -> int:
    """Parse PDFs in parallel, persist sequentially."""
    from scholarforge.ingest.pdf import ParsedPaper, persist_parsed
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

    pdf_files = [f for f in files if f.suffix.lower() == ".pdf"]
    other_files = [f for f in files if f.suffix.lower() != ".pdf"]

    # Filter out already-ingested PDFs
    to_parse = []
    with get_session() as session:
        for f in pdf_files:
            file_hash = hashlib.sha256(f.read_bytes()).hexdigest()
            existing = session.get(Paper, file_hash)
            if existing:
                console.print(f"[dim]Skipping (unchanged):[/dim] {f.name}")
            else:
                to_parse.append(f)

    if not to_parse:
        console.print("[yellow]No new papers to ingest.[/yellow]")
        return 0

    count = 0
    parsed_results: list[ParsedPaper] = []

    # Parse in parallel (CPU-bound: pymupdf4llm)
    console.print(f"[bold]Parsing {len(to_parse)} PDFs with {max_workers} workers...[/bold]")
    with Progress() as progress:
        task = progress.add_task("Parsing PDFs...", total=len(to_parse))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_parse_worker, str(f)): f for f in to_parse}
            for future in as_completed(futures):
                f = futures[future]
                try:
                    parsed = future.result()
                    parsed_results.append(parsed)
                    count += 1
                except Exception as e:
                    console.print(f"[red]Error parsing {f.name}:[/red] {e}")
                progress.advance(task)

    # Persist sequentially (SQLite + vault writes)
    console.print(f"[bold]Persisting {len(parsed_results)} papers...[/bold]")
    for parsed in parsed_results:
        persist_parsed(parsed)
        console.print(
            f"[green]Ingested:[/green] {Path(parsed.paper.source_path).name} "
            f"({len(parsed.chunks)} chunks, {len(parsed.figures)} figures)"
        )

    # Run linking on all papers
    _run_linking()

    # Process other files sequentially
    for f in other_files:
        count += _ingest_file(f)

    return count


def _parse_worker(path_str: str):
    """Worker function for parallel PDF parsing (must be picklable)."""
    from pathlib import Path

    from scholarforge.ingest.pdf import parse_pdf

    return parse_pdf(Path(path_str))


def _run_linking() -> None:
    """Run topic/method linking on all ingested papers."""
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Chunk, Paper
    from scholarforge.vault.linker import link_all_papers

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        papers_with_text = []
        for paper in papers:
            chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
            full_text = "\n\n".join(c.content for c in chunks)
            papers_with_text.append((paper, full_text))

    stats = link_all_papers(papers_with_text)
    console.print(
        f"[green]Linked {stats['papers_linked']} papers → "
        f"{stats['topics']} topics, {stats['methods']} methods[/green]"
    )


def _ingest_file(path: Path) -> int:
    """Ingest a single file based on extension."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from scholarforge.ingest.pdf import ingest_pdf

        return ingest_pdf(path)
    elif ext == ".docx":
        console.print(f"[yellow]DOCX ingestion not yet implemented:[/yellow] {path.name}")
        return 0
    elif ext == ".pptx":
        console.print(f"[yellow]PPTX ingestion not yet implemented:[/yellow] {path.name}")
        return 0
    else:
        console.print(f"[yellow]Unsupported format:[/yellow] {path.name}")
        return 0
