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

    # Run batch steps: linking + embeddings + coupling + vault regeneration
    _run_batch_steps()

    # Process other files sequentially
    for f in other_files:
        count += _ingest_file(f)

    return count


def _parse_worker(path_str: str):
    """Worker function for parallel PDF parsing (must be picklable)."""
    from pathlib import Path

    from scholarforge.ingest.pdf import parse_pdf

    return parse_pdf(Path(path_str))


def _run_batch_steps() -> None:
    """Run all batch post-ingestion steps: linking, embeddings, coupling, vault regen."""
    from sqlmodel import func, select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import embed_abstracts, get_all_similar
    from scholarforge.store.models import Chunk, Citation, FigureRef, Paper
    from scholarforge.vault.coupler import compute_coupling
    from scholarforge.vault.linker import (
        compute_all_links,
        write_method_notes,
        write_topic_notes,
    )
    from scholarforge.vault.writer import _paper_display_name, write_paper_note

    # ── 1. Load all papers + text ────────────────────────────────────────────
    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        papers_with_text: list[tuple[Paper, str]] = []
        for paper in papers:
            chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
            full_text = "\n\n".join(c.content for c in chunks)
            papers_with_text.append((paper, full_text))

    paper_ids = [p.id for p in papers]
    console.print(f"[bold]Running batch steps on {len(papers)} papers...[/bold]")

    # ── 2. Topic/method detection ────────────────────────────────────────────
    per_paper_links = compute_all_links(papers_with_text)
    console.print("[green]  Topics/methods detected[/green]")

    # ── 3. Report citations + figure refs (already persisted during ingestion)
    with get_session() as session:
        total_citations = session.exec(select(func.count(Citation.id))).one()
        total_figure_refs = session.exec(select(func.count(FigureRef.id))).one()
    console.print(
        f"[green]  Found {total_citations} citations, {total_figure_refs} figure refs in DB[/green]"
    )

    # ── 4. Abstract embeddings ───────────────────────────────────────────────
    embedded = embed_abstracts(papers)
    console.print(f"[green]  Embedded {embedded} abstracts into ChromaDB[/green]")

    # ── 5. k-NN similarity ──────────────────────────────────────────────────
    similar_map = get_all_similar(paper_ids, n_results=5)
    console.print("[green]  k-NN similarity computed[/green]")

    # ── 6. Bibliographic coupling ────────────────────────────────────────────
    coupling_map = compute_coupling(paper_ids)
    coupled_count = sum(1 for v in coupling_map.values() if v)
    console.print(f"[green]  Bibliographic coupling: {coupled_count} papers coupled[/green]")

    # ── 7. Build lookup helpers ──────────────────────────────────────────────
    # Map paper_id -> display_name for wikilink resolution
    id_to_display: dict[str, str] = {}
    for paper in papers:
        id_to_display[paper.id] = _paper_display_name(paper)

    # Load figure refs from DB for each paper
    paper_figure_refs: dict[str, list[tuple[str, str]]] = {}
    with get_session() as session:
        for paper in papers:
            frs = session.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
            paper_figure_refs[paper.id] = [(fr.figure_key, fr.caption_text) for fr in frs]

    # ── 8. Write topic/method hub notes ──────────────────────────────────────
    from collections import defaultdict

    topic_papers: dict[str, list[str]] = defaultdict(list)
    method_papers: dict[str, list[str]] = defaultdict(list)
    for paper in papers:
        links = per_paper_links.get(paper.id, {"topics": [], "methods": []})
        display = id_to_display[paper.id]
        for t in links["topics"]:
            topic_papers[t].append(display)
        for m in links["methods"]:
            method_papers[m].append(display)

    write_topic_notes(topic_papers)
    write_method_notes(method_papers)

    # ── 9. Regenerate all paper vault notes with full data ───────────────────
    with get_session() as session:
        for paper in papers:
            chunks_count = len(session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all())
            figures_count = len(
                session.exec(select(FigureRef).where(FigureRef.paper_id == paper.id)).all()
            )

            links = per_paper_links.get(paper.id, {"topics": [], "methods": []})

            # Resolve similar_to IDs to display names
            similar_names = [
                id_to_display[sid] for sid in similar_map.get(paper.id, []) if sid in id_to_display
            ]
            # Resolve coupling IDs to display names
            coupled_names = [
                id_to_display[cid] for cid in coupling_map.get(paper.id, []) if cid in id_to_display
            ]

            write_paper_note(
                paper,
                chunks_count=chunks_count,
                figures_count=figures_count,
                topics=links["topics"],
                methods=links["methods"],
                similar_to=similar_names if similar_names else None,
                cites_same=coupled_names if coupled_names else None,
                figure_refs=paper_figure_refs.get(paper.id) or None,
            )

    console.print(
        f"[green]Batch complete: {len(papers)} paper notes regenerated with all signals[/green]"
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
