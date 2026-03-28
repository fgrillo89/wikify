"""ScholarForge CLI entry point."""

import typer
from rich.console import Console

app = typer.Typer(
    name="scholarforge",
    help="Research writing assistant: ingest papers, build knowledge graphs, generate publications.",
)
console = Console()


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to a PDF file or directory of PDFs"),
):
    """Ingest PDF(s) into the knowledge base."""
    from pathlib import Path

    from scholarforge.ingest.registry import ingest_path

    p = Path(path)
    if not p.exists():
        console.print(f"[red]Path not found:[/red] {path}")
        raise typer.Exit(1)

    count = ingest_path(p)
    console.print(f"[green]Ingested {count} document(s)[/green]")


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
