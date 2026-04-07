"""ScholarForge CLI entry point."""

import logging
from pathlib import Path

import typer
from rich.console import Console

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="wikify",
    help="Research writing assistant: ingest papers, build knowledge graphs, generate.",
)
console = Console()


@app.callback()
def main(
    library: str = typer.Option(
        "default", "--library", "-l", help="Library name (for multi-domain research)"
    ),
):
    """ScholarForge CLI — research writing assistant."""
    if library != "default":
        from wikify.core.config import settings

        settings.library = library
        console.print(f"[dim]Library: {library}[/dim]")


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Path to a PDF file or directory of PDFs"),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Parse PDFs in parallel"),
    workers: int = typer.Option(
        0, "--workers", "-w", help="Parallel workers (0 = 60%% of CPU cores)"
    ),
):
    """Ingest PDF(s) into the knowledge base."""
    import time

    from wikify.ingest.service import ingest_path

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
    from wikify.ingest.corpus_refresh import refresh_corpus

    refresh_corpus()


@app.command()
def stats():
    """Show knowledge base statistics."""
    from sqlmodel import func, select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Chunk, Figure, Paper

    with get_session() as session:
        papers = session.exec(select(func.count(Paper.id))).one()
        chunks = session.exec(select(func.count(Chunk.id))).one()
        figures = session.exec(select(func.count(Figure.id))).one()

    console.print(f"Papers: {papers}  |  Chunks: {chunks}  |  Figures: {figures}")


@app.command()
def graph():
    """Show graph metrics: hubs, bridges, and frontier papers."""

    from sqlmodel import select

    from wikify.core.graph.metrics import compute_metrics
    from wikify.core.store.db import get_session
    from wikify.core.store.models import Paper

    metrics = compute_metrics()

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
    id_to_name = {p.id: p.display_name() for p in papers}

    console.print("\n[bold]Graph Metrics[/bold]\n")
    console.print(metrics.summary_for_llm(id_to_name))

    # Full ranking table
    console.print("\n[bold]Full PageRank Ranking[/bold]")
    sorted_pr = sorted(metrics.pagerank.items(), key=lambda x: x[1], reverse=True)
    for i, (pid, pr) in enumerate(sorted_pr, 1):
        name = id_to_name.get(pid, pid[:12])
        dc = metrics.degree_centrality.get(pid, 0)
        bc = metrics.betweenness_centrality.get(pid, 0)
        role = metrics.paper_role(pid)
        console.print(
            f"  {i:2d}. {name[:60]:<60s}  PR={pr:.3f}  DC={dc:.2f}  BC={bc:.3f}  [{role}]"
        )


@app.command()
def mcp(
    library: str = typer.Option(
        "default", "--library", "-l", help="Library name (for multi-domain research)"
    ),
):
    """Start the ScholarForge MCP server (stdio transport for MCP-compatible clients)."""
    from wikify.mcp_server import run_server

    run_server(library=library)


# ── Sub-CLIs ──────────────────────────────────────────────────────────────────
from wikify.cli.papers import papers_app  # noqa: E402
from wikify.cli.wiki import wiki_app  # noqa: E402

app.add_typer(papers_app)
app.add_typer(wiki_app)

