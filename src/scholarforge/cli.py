"""ScholarForge CLI entry point."""

import typer
from rich.console import Console

app = typer.Typer(
    name="scholarforge",
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
        from scholarforge.config import settings

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
    from pathlib import Path

    from scholarforge.ingest.service import ingest_path

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
    from scholarforge.ingest.corpus_refresh import refresh_corpus

    refresh_corpus()


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


@app.command()
def graph():
    """Show graph metrics: hubs, bridges, and frontier papers."""

    from sqlmodel import select

    from scholarforge.graph.metrics import compute_metrics
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

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
def generate(
    prompt: str = typer.Argument(
        ..., help="Writing prompt, e.g. 'Review of a research topic'"
    ),
    pages: int = typer.Option(10, "--pages", "-n", help="Target page count"),
    output: str = typer.Option("data/output/review.md", "--output", "-o", help="Output file path"),
    journal: str = typer.Option("", "--journal", "-j", help="Target journal for formatting"),
    strategy: str = typer.Option(
        "snowball",
        "--strategy",
        "-s",
        help="Retrieval strategy: flat, hub-spoke, topic-cluster, query-driven, snowball",
    ),
    token_budget: int = typer.Option(12000, "--token-budget", help="Max context tokens"),
    docx: bool = typer.Option(True, "--docx/--no-docx", help="Export DOCX"),
    pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Export PDF"),
):
    """Generate a review paper from the literature corpus."""
    import time
    from pathlib import Path

    from scholarforge.export.journal_profile import load_journal_profile
    from scholarforge.generate.planner import plan_paper
    from scholarforge.generate.writer import write_paper
    from scholarforge.retrieve.strategies import StrategyConfig, get_strategy

    start = time.time()
    journal_profile = load_journal_profile(journal)
    if journal:
        console.print(f"[dim]Journal: {journal_profile.name}[/dim]")

    config = StrategyConfig(token_budget=token_budget, user_focus=prompt)
    retriever = get_strategy(strategy, config=config)
    console.print(f"[bold]Retrieving literature[/bold] (strategy: {strategy})...")

    if retriever.expensive:
        cost = retriever.estimate_cost()
        console.print(
            f"  [yellow]Strategy uses ~{cost['llm_calls']:.0f} LLM calls"
            f" (~${cost['est_usd']:.4f})[/yellow]"
        )

    context = retriever.retrieve()
    console.print(f"  {len(context.papers)} papers, {context.total_tokens} tokens of context")

    console.print("[bold]Planning paper structure...[/bold]")
    plan = plan_paper(prompt, context, target_pages=pages, journal_profile=journal_profile)
    console.print(f"  Title: {plan.title}")
    console.print(f"  Sections: {len(plan.sections)}")

    console.print("[bold]Writing paper...[/bold]")
    result = write_paper(plan, context, journal_profile=journal_profile)
    paper_md, ordered_papers = result  # type: ignore[misc]

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(paper_md, encoding="utf-8")

    elapsed = time.time() - start
    word_count = len(paper_md.split())
    console.print(
        f"[green]Generated {word_count} words ({word_count // 250} pages) "
        f"in {elapsed:.1f}s → {out_path}[/green]"
    )
    if ordered_papers:
        console.print(f"  References: {len(ordered_papers)}")

    # Export to additional formats
    if docx:
        from scholarforge.export.docx_export import DocxExporter

        docx_path = out_path.with_suffix(".docx")
        DocxExporter(journal_profile).export(paper_md, ordered_papers, docx_path)
        console.print(f"[green]DOCX:[/green] {docx_path}")

    if pdf:
        from scholarforge.export.pdf_export import PdfExporter

        pdf_path = out_path.with_suffix(".pdf")
        PdfExporter(journal_profile).export(paper_md, ordered_papers, pdf_path)
        console.print(f"[green]PDF:[/green] {pdf_path}")


@app.command("agent-generate")
def agent_generate(
    prompt: str = typer.Argument(..., help="What to write"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model (default from config)"),
    journal: str = typer.Option("", "--journal", "-j", help="Target journal"),
    artifact_type: str = typer.Option("lit_review", "--type", "-t", help="Document type"),
    token_budget: int = typer.Option(200_000, "--token-budget", help="Max tokens"),
    max_turns: int = typer.Option(30, "--max-turns", help="Max agent turns"),
    output: str = typer.Option("data/output/paper.md", "--output", "-o"),
    docx: bool = typer.Option(True, "--docx/--no-docx", help="Export DOCX"),
    pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Export PDF"),
):
    """Generate a paper using the agent loop (LLM explores corpus via tools)."""
    from scholarforge.agent.workflows import export_paper, generate_paper

    console.print(f"[bold]Generating with agent loop[/bold] (model: {model or 'default'})...")
    console.print(f"  Type: {artifact_type}, Journal: {journal or 'generic'}")
    console.print(f"  Token budget: {token_budget:,}, Max turns: {max_turns}")

    markdown, result, hooks = generate_paper(
        prompt=prompt,
        model=model,
        artifact_type_id=artifact_type,
        journal=journal,
        token_budget=token_budget,
        max_turns=max_turns,
    )

    console.print("\n[green]Generation complete:[/green]")
    console.print(f"  Turns: {result.total_turns}")
    console.print(f"  Tool calls: {len(result.tool_calls)}")
    console.print(
        f"  Tokens: {result.total_input_tokens:,} in + {result.total_output_tokens:,} out"
    )

    # Show cost from the CostTracker hook
    for hook in hooks:
        if hasattr(hook, "summary"):
            console.print(f"  Cost: {hook.summary()}")

    # Export
    outputs = export_paper(markdown, output, journal, docx, pdf)
    for p in outputs:
        console.print(f"  Written: {p} ({p.stat().st_size:,} bytes)")


@app.command("scripted-generate")
def scripted_generate(
    prompt: str = typer.Argument("research topic", help="Review topic"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model (litellm format)"),
    summarize_model: str = typer.Option(
        None, "--summarize-model", help="Model for paper summarization (default: same as --model)"
    ),
    write_model: str = typer.Option(
        None, "--write-model", help="Model for review writing (default: same as --model)"
    ),
    journal: str = typer.Option("", "--journal", "-j", help="Target journal"),
    artifact_type: str = typer.Option("lit_review", "--type", "-t", help="Document type"),
    output: str = typer.Option("data/output/review_scripted.md", "--output", "-o"),
    max_papers: int = typer.Option(12, "--max-papers", help="Papers in exploration order"),
    n_deep: int = typer.Option(3, "--n-deep", help="Papers to deep-read"),
    word_target: int = typer.Option(4000, "--words", "-w", help="Target word count"),
):
    """Generate a review using scripted exploration + LLM writing.

    Unlike agent-generate, the exploration is deterministic Python code.
    The LLM is used only for paper summarization and final writing.
    Supports local models (e.g., ollama/qwen2.5:14b).
    """
    from scholarforge.agent.scripted import run_scripted

    console.print("[bold]Scripted generation[/bold]")
    console.print(f"  Topic: {prompt}")
    console.print(f"  Model: {write_model or model or 'default'}")
    console.print(f"  Papers: {max_papers} ({n_deep} deep reads)")
    console.print(f"  Target: {word_target} words")

    result = run_scripted(
        topic=prompt,
        model=model,
        summarize_model=summarize_model,
        write_model=write_model,
        max_papers=max_papers,
        n_deep=n_deep,
        word_target=word_target,
        artifact_type_id=artifact_type,
        journal=journal,
        output_path=output,
    )

    console.print("\n[green]Scripted generation complete:[/green]")
    console.print(f"  Papers read: {result.papers_read}")
    console.print(f"  Explore: {result.explore_time_s:.0f}s")
    console.print(f"  Summarize: {result.summarize_time_s:.0f}s")
    console.print(f"  Write: {result.write_time_s:.0f}s")
    console.print(f"  Total: {result.total_time_s:.0f}s ({result.total_time_s / 60:.1f}m)")
    console.print(f"  Words: {len(result.review_text.split())}")
    console.print(f"  Tokens: {result.tokens_in:,} in + {result.tokens_out:,} out")


@app.command("fast-generate")
def fast_generate_cmd(
    prompt: str = typer.Argument("research topic", help="Review topic"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
    journal: str = typer.Option("", "--journal", "-j", help="Target journal"),
    output: str = typer.Option("data/output/review_fast.md", "--output", "-o"),
    max_papers: int = typer.Option(15, "--max-papers"),
    word_target: int = typer.Option(4000, "--words", "-w"),
):
    """EXPERIMENTAL: Fast one-shot generation (pre-compute + single LLM call).

    Pre-computes all context offline (frontier order, gaps, digests, concept
    links), then writes the review in a single LLM call. Much faster than
    agent-generate but may produce lower quality. Use for rapid iteration.
    """
    from scholarforge.agent.fast_generate import fast_generate

    console.print("[bold]Fast generation (experimental)[/bold]")
    console.print(f"  Topic: {prompt}")
    console.print(f"  Model: {model or 'default'}")

    result = fast_generate(
        topic=prompt,
        model=model,
        word_target=word_target,
        max_papers=max_papers,
        journal=journal,
        output_path=output,
    )

    console.print("\n[green]Fast generation complete:[/green]")
    console.print(f"  Pre-compute: {result.precompute_time_s:.0f}s")
    console.print(f"  LLM write: {result.llm_time_s:.0f}s")
    console.print(f"  Total: {result.total_time_s:.0f}s ({result.total_time_s / 60:.1f}m)")
    console.print(f"  Context: {result.context_chars:,} chars")
    console.print(f"  Words: {len(result.review_text.split())}")
    console.print(f"  Tokens: {result.tokens_in:,} in + {result.tokens_out:,} out")
    console.print(f"  Papers: {result.papers_used}")


@app.command()
def slides(
    prompt: str = typer.Argument(..., help="Presentation topic"),
    num_slides: int = typer.Option(10, "--slides", "-n", help="Number of slides"),
    output: str = typer.Option(
        "data/output/presentation.pptx", "--output", "-o", help="Output PPTX path"
    ),
):
    """Generate a PowerPoint presentation from the literature corpus."""
    import time
    from pathlib import Path

    from scholarforge.export.pptx_export import export_slides
    from scholarforge.generate.planner import plan_slides
    from scholarforge.retrieve.context import retrieve_all_papers

    start = time.time()

    console.print("[bold]Retrieving literature...[/bold]")
    context = retrieve_all_papers()

    console.print("[bold]Planning slides...[/bold]")
    slide_plan = plan_slides(prompt, context, num_slides=num_slides)
    console.print(f"  {len(slide_plan)} slides planned")

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_slides(slide_plan, out_path, title=prompt)

    elapsed = time.time() - start
    console.print(
        f"[green]Generated {len(slide_plan)} slides in {elapsed:.1f}s → {out_path}[/green]"
    )


@app.command()
def chat():
    """Interactive chat with the literature corpus."""
    from scholarforge.generate.chat import chat_interactive

    chat_interactive()


@app.command()
def evaluate(
    review_path: str = typer.Argument(..., help="Path to the review markdown file"),
    pi: bool = typer.Option(False, "--pi", help="Run LLM-as-PI qualitative scoring"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model (litellm format)"),
    domain: str = typer.Option(
        "", "--domain", "-d", help="One-line field description for PI context"
    ),
):
    """Evaluate a generated review with automated metrics and/or LLM-as-PI scoring."""
    from pathlib import Path

    path = Path(review_path)
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)

    review_text = path.read_text(encoding="utf-8")

    if not pi:
        # Automated metrics only
        from scholarforge.evaluate.quality import comprehensive_quality_report

        console.print("[bold]Running automated quality metrics...[/bold]")
        report = comprehensive_quality_report(review_text)
        console.print(report.summary())
    else:
        # LLM-as-PI qualitative scoring
        from scholarforge.evaluate.pi_review import evaluate_pi, parse_pi_scores

        console.print("[bold]Running LLM-as-PI review...[/bold]")
        if domain:
            console.print(f"  Domain hint: {domain}")

        pi_report = evaluate_pi(review_text, domain_hint=domain, model=model)
        console.print(pi_report)

        scores = parse_pi_scores(pi_report)
        if "overall" in scores:
            overall = scores["overall"]
            color = "green" if overall >= 8 else "yellow" if overall >= 6 else "red"
            console.print(f"\n[{color}]Overall PI score: {overall}/10[/{color}]")

        # Optionally save alongside the review
        out_path = path.with_suffix(".pi_review.md")
        out_path.write_text(pi_report, encoding="utf-8")
        console.print(f"[dim]Saved PI review to: {out_path}[/dim]")


@app.command()
def mcp(
    library: str = typer.Option(
        "default", "--library", "-l", help="Library name (for multi-domain research)"
    ),
):
    """Start the ScholarForge MCP server (stdio transport for Claude Code / LLM clients).

    Example usage in Claude Code settings:
        {
          "mcpServers": {
            "scholarforge": {
              "command": "scholarforge",
              "args": ["mcp"]
            }
          }
        }
    """
    from scholarforge.mcp_server import run_server

    run_server(library=library)


# ── Template management commands ──────────────────────────────────────────────

templates_app = typer.Typer(help="Manage journal templates (DOCX/LaTeX).")
app.add_typer(templates_app, name="templates")


@templates_app.command("list")
def templates_list():
    """List all available journal templates (from SQLite + filesystem)."""
    from rich.table import Table

    from scholarforge.export.templates.registry import list_templates

    items = list_templates()
    if not items:
        console.print("[yellow]No templates imported yet.[/yellow]")
        console.print("Run: scholarforge templates sources")
        return

    table = Table(title="Available Templates")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Publisher")
    table.add_column("Type")
    table.add_column("Source")
    table.add_column("Status")
    for item in items:
        table.add_row(
            item["id"],
            item["name"],
            item.get("publisher", ""),
            item["type"],
            item["source"],
            item.get("status", "ok"),
        )
    console.print(table)


@templates_app.command("sources")
def templates_sources():
    """Show known publisher template sources and download instructions."""
    from scholarforge.export.templates.registry import show_download_instructions

    show_download_instructions()


@templates_app.command("download")
def templates_download(
    template_id: str = typer.Argument(..., help="Template ID (e.g., wiley_afm, nature, acs, ieee)"),
):
    """Download a publisher template automatically (bypasses Cloudflare).

    Uses a stealth browser under the hood — no visible window.
    """
    from scholarforge.export.templates.registry import download_template

    result = download_template(template_id)
    if result:
        console.print(f'[green]Ready to use.[/green] template_docx: "{template_id}"')


@templates_app.command("import")
def templates_import(
    path: str = typer.Argument(..., help="Path to a .docx or .cls file"),
    name: str = typer.Option("", "--name", "-n", help="Name/ID for the template"),
    publisher: str = typer.Option("", "--publisher", "-p", help="Publisher name"),
):
    """Import a .docx file as a reusable template.

    Use this to import a publisher template you downloaded, or
    your own paper's formatting as a template for future papers.

    Examples:
        scholarforge templates import wiley_template.docx --name "wiley_afm"
        scholarforge templates import my_old_paper.docx --name "my_style"
    """
    from pathlib import Path

    from scholarforge.export.templates.registry import import_template

    import_template(Path(path), name=name, publisher=publisher)


@templates_app.command("styles")
def templates_styles(
    path: str = typer.Argument(..., help="Path to a .docx file to inspect"),
):
    """Show all Word styles defined in a .docx file.

    Useful for setting up the style_map in a journal profile.
    """
    from pathlib import Path

    from rich.table import Table

    from scholarforge.export.templates.registry import extract_styles, suggest_style_map

    docx_path = Path(path)
    styles = extract_styles(docx_path)
    suggested = suggest_style_map(docx_path)

    table = Table(title=f"Styles in {docx_path.name}")
    table.add_column("Style Name")
    table.add_column("Type")
    table.add_column("Mapped To")
    for sname, stype in sorted(styles.items()):
        role = ""
        for r, s in suggested.items():
            if s == sname:
                role = r
                break
        table.add_row(sname, stype, role or "")
    console.print(table)

    console.print("\n[bold]Suggested style_map:[/bold]")
    for role, style in suggested.items():
        console.print(f"  {role}: {style}")


if __name__ == "__main__":
    app()
