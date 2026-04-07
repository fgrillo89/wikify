"""Public ingestion service API."""

from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

from wikify.ingest.corpus_refresh import (
    refresh_corpus,
    run_background_refresh,
    run_incremental_refresh,
)

console = Console()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html", ".htm"}


def default_workers() -> int:
    """Return 60 percent of CPU cores, minimum 2."""
    import os

    return max(2, int(os.cpu_count() * 0.6))


def ingest_path(path: Path, parallel: bool = False, max_workers: int = 0) -> int:
    """Ingest a file or directory. Returns count of documents ingested."""
    if max_workers <= 0:
        max_workers = default_workers()
    if path.is_file():
        return ingest_file(path)
    if path.is_dir():
        files: list[Path] = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(sorted(path.rglob(f"*{ext}")))
        if not files:
            return 0

        if parallel and len(files) > 1:
            return _ingest_parallel(files, max_workers)

        count = 0
        for file in files:
            count += ingest_file(file, background_refresh=False)
        if count > 0:
            refresh_corpus()
        return count
    return 0


def ingest_file(path: Path, background_refresh: bool = True) -> int:
    """Ingest a single file based on extension.

    Dispatches by suffix:
      .pdf              -> pdf ingester
      .docx             -> docx ingester
      .pptx             -> pptx ingester
      .md, .txt         -> markdown ingester
      .html, .htm       -> html ingester
    """
    ext = path.suffix.lower()
    paper_id: str | None = None

    if ext == ".pdf":
        from wikify.ingest.pdf import ingest_pdf

        paper_id = ingest_pdf(path, return_id=True)
    elif ext == ".docx":
        from wikify.ingest.docx import ingest_docx

        paper_id = ingest_docx(path, return_id=True)
    elif ext == ".pptx":
        from wikify.ingest.pptx import ingest_pptx

        paper_id = ingest_pptx(path, return_id=True)
    elif ext in {".md", ".txt"}:
        from wikify.ingest.markdown import ingest_markdown

        paper_id = ingest_markdown(path, return_id=True)
    elif ext in {".html", ".htm"}:
        from wikify.ingest.html import ingest_html

        paper_id = ingest_html(path, return_id=True)
    else:
        console.print(f"[yellow]Unsupported format:[/yellow] {path.name}")
        return 0

    if not paper_id:
        return 0

    if background_refresh:
        run_incremental_refresh(paper_id)
        run_background_refresh()

    return 1


def _ingest_parallel(files: list[Path], max_workers: int) -> int:
    """Parse PDFs in parallel, then persist sequentially."""
    from wikify.core.store.db import get_session
    from wikify.core.store.models import Paper
    from wikify.ingest.pdf import ParsedPaper, persist_parsed

    pdf_files = [file for file in files if file.suffix.lower() == ".pdf"]
    other_files = [file for file in files if file.suffix.lower() != ".pdf"]

    to_parse: list[Path] = []
    with get_session() as session:
        for file in pdf_files:
            file_hash = hashlib.sha256(file.read_bytes()).hexdigest()
            existing = session.get(Paper, file_hash)
            if existing:
                console.print(f"[dim]Skipping (unchanged):[/dim] {file.name}")
            else:
                to_parse.append(file)

    if not to_parse:
        console.print("[yellow]No new papers to ingest.[/yellow]")
        return 0

    count = 0
    parsed_results: list[ParsedPaper] = []

    console.print(f"[bold]Parsing {len(to_parse)} PDFs with {max_workers} workers...[/bold]")
    with Progress() as progress:
        task = progress.add_task("Parsing PDFs...", total=len(to_parse))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_parse_worker, str(file)): file for file in to_parse}
            for future in as_completed(futures):
                file = futures[future]
                try:
                    parsed = future.result()
                    parsed_results.append(parsed)
                    count += 1
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]Error parsing {file.name}:[/red] {exc}")
                progress.advance(task)

    console.print(f"[bold]Persisting {len(parsed_results)} papers...[/bold]")
    for parsed in parsed_results:
        persist_parsed(parsed)
        console.print(
            f"[green]Ingested:[/green] {Path(parsed.paper.source_path).name} "
            f"({len(parsed.chunks)} chunks, {len(parsed.figures)} figures)"
        )

    new_ids = {parsed.paper.id for parsed in parsed_results}
    refresh_corpus(new_paper_ids=new_ids)

    for file in other_files:
        count += ingest_file(file)

    return count


def _parse_worker(path_str: str):
    """Worker function for parallel PDF parsing."""
    from wikify.ingest.pdf import parse_pdf

    return parse_pdf(Path(path_str))
