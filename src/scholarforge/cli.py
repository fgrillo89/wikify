"""ScholarForge CLI entry point."""

import typer
from rich.console import Console

app = typer.Typer(
    name="scholarforge",
    help="Research writing assistant: ingest papers, build knowledge graphs, generate.",
)
console = Console()


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to a PDF file or directory of PDFs"),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Parse PDFs in parallel"),
    workers: int = typer.Option(4, "--workers", "-w", help="Number of parallel workers"),
):
    """Ingest PDF(s) into the knowledge base."""
    import time
    from pathlib import Path

    from scholarforge.ingest.registry import ingest_path

    p = Path(path)
    if not p.exists():
        console.print(f"[red]Path not found:[/red] {path}")
        raise typer.Exit(1)

    start = time.time()
    count = ingest_path(p, parallel=parallel, max_workers=workers)
    elapsed = time.time() - start
    rate = f" ({elapsed / count:.1f}s/paper)" if count > 0 else ""
    console.print(f"[green]Ingested {count} document(s) in {elapsed:.1f}s{rate}[/green]")


@app.command()
def refresh():
    """Full refresh: recompute all topics, embeddings, similarity, coupling, and vault notes."""
    from scholarforge.ingest.registry import _run_batch_steps

    _run_batch_steps()


@app.command()
def stats():
    """Show knowledge base statistics."""
    from sqlmodel import func, select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Chunk, Figure, Paper

    with get_session() as session:
        papers = session.exec(select(func.count(Paper.id))).one()
        chunks = session.exec(select(func.count(Chunk.id))).one()
        figures = session.exec(select(func.count(Figure.id))).one()

    console.print(f"Papers: {papers}  |  Chunks: {chunks}  |  Figures: {figures}")


if __name__ == "__main__":
    app()
