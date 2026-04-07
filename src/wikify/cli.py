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
        from wikify.config import settings

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

    from wikify.store.db import get_session
    from wikify.store.models import Chunk, Figure, Paper

    with get_session() as session:
        papers = session.exec(select(func.count(Paper.id))).one()
        chunks = session.exec(select(func.count(Chunk.id))).one()
        figures = session.exec(select(func.count(Figure.id))).one()

    console.print(f"Papers: {papers}  |  Chunks: {chunks}  |  Figures: {figures}")


@app.command()
def graph():
    """Show graph metrics: hubs, bridges, and frontier papers."""

    from sqlmodel import select

    from wikify.graph.metrics import compute_metrics
    from wikify.store.db import get_session
    from wikify.store.models import Paper

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
    prompt: str = typer.Argument(..., help="Writing prompt, e.g. 'Review of a research topic'"),
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

    from wikify.papers.export.journal_profile import load_journal_profile
    from wikify.papers.generate.planner import plan_paper
    from wikify.papers.generate.writer import write_paper
    from wikify.papers.retrieve.strategies import StrategyConfig, get_strategy

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
        from wikify.papers.export.docx_export import DocxExporter

        docx_path = out_path.with_suffix(".docx")
        DocxExporter(journal_profile).export(paper_md, ordered_papers, docx_path)
        console.print(f"[green]DOCX:[/green] {docx_path}")

    if pdf:
        from wikify.papers.export.pdf_export import PdfExporter

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
    from wikify.papers.agent.workflows import export_paper, generate_paper

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
    from wikify.papers.agent.scripted import run_scripted

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
    from wikify.papers.agent.fast_generate import fast_generate

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

    from wikify.papers.export.pptx_export import export_slides
    from wikify.papers.generate.planner import plan_slides
    from wikify.papers.retrieve.context import retrieve_all_papers

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
    from wikify.papers.generate.chat import chat_interactive

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

    path = Path(review_path)
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)

    review_text = path.read_text(encoding="utf-8")

    if not pi:
        # Automated metrics only
        from wikify.papers.evaluate.quality import comprehensive_quality_report

        console.print("[bold]Running automated quality metrics...[/bold]")
        report = comprehensive_quality_report(review_text)
        console.print(report.summary())
    else:
        # LLM-as-PI qualitative scoring
        from wikify.papers.evaluate.pi_review import evaluate_pi, parse_pi_review

        console.print("[bold]Running LLM-as-PI review...[/bold]")
        if domain:
            console.print(f"  Domain hint: {domain}")

        pi_report = evaluate_pi(review_text, domain_hint=domain, model=model)
        result = parse_pi_review(pi_report)

        console.print(result.report)

        if result.overall_score is not None:
            overall = result.overall_score
            color = "green" if overall >= 8 else "yellow" if overall >= 6 else "red"
            console.print(f"\n[{color}]Overall PI score: {overall}/10[/{color}]")

        if result.weakest_section:
            console.print(f"[dim]Weakest section: {result.weakest_section}[/dim]")

        # Optionally save alongside the review
        out_path = path.with_suffix(".pi_review.md")
        out_path.write_text(pi_report, encoding="utf-8")
        console.print(f"[dim]Saved PI review to: {out_path}[/dim]")


@app.command()
def revise(
    review_path: str = typer.Argument(..., help="Path to the review markdown file to revise"),
    topic: str = typer.Option("", "--topic", "-t", help="Topic/prompt that generated the review"),
    domain: str = typer.Option(
        "", "--domain", "-d", help="One-line field description for PI context"
    ),
    model: str = typer.Option(None, "--model", "-m", help="LLM model (litellm format)"),
    output: str = typer.Option(
        "", "--output", "-o", help="Output path (default: <review>.revised.md)"
    ),
):
    """Run PI review on a generated review, then rewrite its weakest section.

    Steps:
      1. Run LLM-as-PI evaluation.
      2. Identify the weakest section.
      3. Fetch targeted corpus evidence for that section.
      4. Rewrite the section with fresh evidence.
      5. Save the revised review.
    """

    from wikify.papers.agent.revision import revise_weakest_section
    from wikify.papers.evaluate.pi_review import evaluate_pi, parse_pi_review

    path = Path(review_path)
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)

    review_text = path.read_text(encoding="utf-8")

    console.print("[bold]Step 1: Running LLM-as-PI review...[/bold]")
    pi_report = evaluate_pi(review_text, domain_hint=domain, model=model)
    pi_result = parse_pi_review(pi_report)

    if pi_result.overall_score is not None:
        overall = pi_result.overall_score
        color = "green" if overall >= 8 else "yellow" if overall >= 6 else "red"
        console.print(f"[{color}]PI score: {overall}/10[/{color}]")

    if not pi_result.weakest_section:
        console.print("[yellow]PI review did not identify a weakest section.[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]Step 2: Revising section:[/bold] {pi_result.weakest_section}")

    revised = revise_weakest_section(
        review_text,
        pi_result,
        topic=topic,
        model=model,
    )

    out_path = Path(output) if output else path.with_suffix(".revised.md")
    out_path.write_text(revised, encoding="utf-8")

    word_delta = len(revised.split()) - len(review_text.split())
    console.print(f"[green]Revised review saved to: {out_path}[/green]")
    console.print(f"[dim]Word count delta: {word_delta:+d}[/dim]")

    # Save PI review alongside
    pi_path = path.with_suffix(".pi_review.md")
    pi_path.write_text(pi_report, encoding="utf-8")
    console.print(f"[dim]PI review saved to: {pi_path}[/dim]")


@app.command()
def mcp(
    library: str = typer.Option(
        "default", "--library", "-l", help="Library name (for multi-domain research)"
    ),
):
    """Start the ScholarForge MCP server (stdio transport for MCP-compatible clients).

    Example usage in an MCP client config:
        {
          "mcpServers": {
            "wikify": {
              "command": "wikify",
              "args": ["mcp"]
            }
          }
        }
    """
    from wikify.mcp_server import run_server

    run_server(library=library)


# ── Template management commands ──────────────────────────────────────────────

templates_app = typer.Typer(help="Manage journal templates (DOCX/LaTeX).")
app.add_typer(templates_app, name="templates")


@templates_app.command("list")
def templates_list():
    """List all available journal templates (from SQLite + filesystem)."""
    from rich.table import Table

    from wikify.papers.export.templates.registry import list_templates

    items = list_templates()
    if not items:
        console.print("[yellow]No templates imported yet.[/yellow]")
        console.print("Run: wikify templates sources")
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
    from wikify.papers.export.templates.registry import show_download_instructions

    show_download_instructions()


@templates_app.command("download")
def templates_download(
    template_id: str = typer.Argument(..., help="Template ID (e.g., wiley_afm, nature, acs, ieee)"),
):
    """Download a publisher template automatically (bypasses Cloudflare).

    Uses a stealth browser under the hood — no visible window.
    """
    from wikify.papers.export.templates.registry import download_template

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
        wikify templates import wiley_template.docx --name "wiley_afm"
        wikify templates import my_old_paper.docx --name "my_style"
    """

    from wikify.papers.export.templates.registry import import_template

    import_template(Path(path), name=name, publisher=publisher)


@templates_app.command("styles")
def templates_styles(
    path: str = typer.Argument(..., help="Path to a .docx file to inspect"),
):
    """Show all Word styles defined in a .docx file.

    Useful for setting up the style_map in a journal profile.
    """

    from rich.table import Table

    from wikify.papers.export.templates.registry import extract_styles, suggest_style_map

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


# ── Wiki commands ─────────────────────────────────────────────────────────────

wiki_app = typer.Typer(name="wiki", help="Build and maintain the curated wiki layer.")
app.add_typer(wiki_app)


@wiki_app.command("init")
def wiki_init(
    topic: str = typer.Option("", "--topic", help="Optional topic hint to focus exploration"),
    max_papers: int = typer.Option(
        20, "--max-papers", help="Max sources to read during corpus exploration"
    ),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
    resume: bool = typer.Option(False, "--resume", help="Skip articles whose files already exist"),
):
    """Bootstrap the wiki from the corpus using the two-phase sitemap pipeline.

    Phase 1 explores the corpus broadly to discover thematic structure.
    Phase 2 generates a structured article plan (sitemap).
    Phase 3 writes all articles in dependency order (themes then concepts).
    """

    from wikify.wiki.legacy.agent import build_wiki_from_sitemap
    from wikify.wiki.builder import generate_wiki_index
    from wikify.wiki.linker import cross_link_articles, ensure_parent_backlinks
    from wikify.wiki.legacy.sitemap import generate_sitemap

    wiki_dir = Path("data/wiki")
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1 + 2: explore corpus and generate structured sitemap
    console.print("[bold]Phase 1: Exploring corpus...[/bold]")
    sitemap = generate_sitemap(
        topic_hint=topic,
        model=model,
        wiki_dir=wiki_dir,
        max_explore_papers=max_papers,
        run_context=None,
    )
    theme_count = len(sitemap.themes())
    concept_count = len(sitemap.concepts())
    total = len(sitemap.entries)
    console.print(f"  Planned {total} articles: {theme_count} themes, {concept_count} concepts")

    # Phase 3: write articles
    console.print(f"[bold]Phase 2: Writing {theme_count} theme articles...[/bold]")
    build_wiki_from_sitemap(sitemap, wiki_dir, model=model, resume=resume)

    # Cross-link
    linked = cross_link_articles(wiki_dir, sitemap)
    console.print(f"  Cross-linked {linked} articles")

    # Parent backlinks
    ensure_parent_backlinks(wiki_dir, sitemap)

    # Index
    generate_wiki_index(wiki_dir)
    index_path = wiki_dir / "_index.md"
    console.print(f"[green]Wiki index written to {index_path}[/green]")

    console.print(
        f"[green]Wiki built: {theme_count} themes, {concept_count} concepts,"
        f" {total} articles[/green]"
    )


@wiki_app.command("expand")
def wiki_expand(
    concept: str = typer.Argument("", help="Concept slug or title to expand"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
    all_stubs: bool = typer.Option(False, "--all", help="Expand all stubs/drafts"),
):
    """Expand a stub/draft wiki article into a full article.

    If CONCEPT is given, expands that specific article.
    If --all is given, expands every stub and draft in the sitemap.
    Falls back to build_wiki_article when no sitemap exists.
    """
    import json
    from datetime import datetime, timezone

    from sqlmodel import Session, select

    from wikify.store.db import get_engine
    from wikify.store.models import WikiArticle
    from wikify.wiki.builder import (
        article_path,
        generate_wiki_index,
        slugify,
        write_article,
    )
    from wikify.wiki.linker import cross_link_articles
    from wikify.wiki.legacy.sitemap import WikiSitemap

    wiki_dir = Path("data/wiki")
    sitemap = WikiSitemap.load(wiki_dir)
    engine = get_engine()

    def _expand_entry(entry: "WikiSitemap.entries.__class__") -> None:  # type: ignore[name-defined]
        from wikify.wiki.legacy.agent import build_article_from_entry

        content, source_ids = build_article_from_entry(entry, wiki_dir, model=model)

        category_dir = {
            "theme": "concepts",
            "concept": "concepts",
            "synthesis": "syntheses",
            "query": "queries",
        }.get(entry.category, "concepts")
        out_path = article_path(wiki_dir, category_dir, entry.slug)

        write_article(
            path=out_path,
            title=entry.title,
            content=content,
            sources=source_ids,
            topics=[entry.slug] + entry.related_slugs,
            status="full",
            model=model or "",
        )

        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            row = session.exec(select(WikiArticle).where(WikiArticle.id == entry.slug)).first()
            if row is None:
                row = WikiArticle(
                    id=entry.slug,
                    title=entry.title,
                    status="full",
                    file_path=str(out_path.relative_to(wiki_dir.parent)),
                    source_ids=json.dumps(source_ids),
                    topic_keys=json.dumps([entry.slug]),
                    created_at=now,
                    updated_at=now,
                    model=model or "",
                    needs_update=False,
                )
            else:
                row.status = "full"
                row.source_ids = json.dumps(source_ids)
                row.updated_at = now
                row.model = model or row.model
                row.needs_update = False
            session.add(row)
            session.commit()

        console.print(f"  Expanded: {entry.title}")

    if sitemap is not None:
        if all_stubs:
            targets = [e for e in sitemap.entries if e.depth in ("stub", "draft")]
        elif concept:
            slug = slugify(concept)
            by_slug = sitemap.by_slug()
            entry = by_slug.get(slug)
            if entry is None:
                # Try title match
                matches = [e for e in sitemap.entries if concept.lower() in e.title.lower()]
                if not matches:
                    console.print(f"[red]No sitemap entry found for: {concept}[/red]")
                    raise typer.Exit(1)
                entry = matches[0]
            targets = [entry]
        else:
            console.print("[yellow]Provide a CONCEPT or --all to expand stubs.[/yellow]")
            raise typer.Exit(1)

        if not targets:
            console.print("[yellow]No stubs to expand.[/yellow]")
            return

        console.print(f"[bold]Expanding {len(targets)} article(s)...[/bold]")
        for entry in targets:
            try:
                _expand_entry(entry)
            except Exception as exc:
                console.print(f"[red]  Failed ({entry.title}): {exc}[/red]")

        # After expanding: cross-link and regenerate index
        cross_link_articles(wiki_dir, sitemap)
        generate_wiki_index(wiki_dir)

    else:
        # No sitemap: fall back to build_wiki_article for the given concept string
        if not concept:
            console.print("[red]No sitemap found and no concept given.[/red]")
            raise typer.Exit(1)

        from wikify.wiki.legacy.agent import build_wiki_article

        console.print(f"[bold]Expanding (no-sitemap fallback): {concept}[/bold]")
        content, source_ids = build_wiki_article(concept, concept, status="full", model=model)

        slug = slugify(concept)
        out_path = article_path(wiki_dir, "concepts", slug)
        write_article(
            path=out_path,
            title=concept,
            content=content,
            sources=source_ids,
            topics=[slug],
            status="full",
            model=model or "",
        )

        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            row = WikiArticle(
                id=slug,
                title=concept,
                status="full",
                file_path=str(out_path.relative_to(wiki_dir.parent)),
                source_ids=json.dumps(source_ids),
                topic_keys=json.dumps([slug]),
                created_at=now,
                updated_at=now,
                model=model or "",
                needs_update=False,
            )
            session.merge(row)
            session.commit()

        cross_link_articles(wiki_dir, None)
        generate_wiki_index(wiki_dir)
        console.print(f"[green]Expanded: {concept} -> {out_path}[/green]")

    console.print("[green]Expand complete.[/green]")


@wiki_app.command("sync")
def wiki_sync(
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
):
    """Update stale wiki articles (needs_update=True) with new corpus evidence.

    For each stale article:
    1. Map new (uncovered) sources to the article topic.
    2. Run detect_contradiction on each relevant extraction.
    3. Route to additive_update or revisionary_update accordingly.
    4. Write the updated body back to file (preserving frontmatter).
    """
    import json
    from datetime import datetime, timezone

    from sqlmodel import Session, select

    from wikify.store.db import get_engine
    from wikify.store.models import SourceCoverage, WikiArticle
    from wikify.wiki.builder import generate_wiki_index, write_article
    from wikify.wiki.maintenance import (
        _strip_frontmatter,
        additive_update,
        detect_contradiction,
        revisionary_update,
    )
    from wikify.wiki.mapreduce import map_chunks_to_topic
    from wikify.wiki.persona import get_or_create_persona

    wiki_dir = Path("data/wiki")
    engine = get_engine()

    with Session(engine) as session:
        stale = list(session.exec(select(WikiArticle).where(WikiArticle.needs_update)).all())

    if not stale:
        console.print("[green]Synced 0 articles[/green]")
        return

    console.print(f"[bold]Syncing {len(stale)} stale article(s)...[/bold]")
    synced = 0
    additive_count = 0
    revisionary_count = 0

    for article in stale:
        console.print(f"  Syncing: {article.title}")
        art_path = Path(article.file_path)
        if not art_path.is_absolute():
            art_path = Path("data") / article.file_path
        if not art_path.exists():
            console.print(f"[yellow]  File missing, skipping: {art_path}[/yellow]")
            continue

        # Identify source IDs already covered for this article
        source_ids: list[str] = json.loads(article.source_ids or "[]")
        with Session(engine) as session:
            covered_rows = list(
                session.exec(
                    select(SourceCoverage.source_id).where(
                        SourceCoverage.article_slug == article.id
                    )
                ).all()
            )
        covered_ids: set[str] = set(covered_rows)
        new_source_ids = [pid for pid in source_ids if pid not in covered_ids]

        if not new_source_ids:
            # Nothing new — just clear the flag
            with Session(engine) as session:
                row = session.exec(select(WikiArticle).where(WikiArticle.id == article.id)).first()
                if row is not None:
                    row.needs_update = False
                    row.updated_at = datetime.now(timezone.utc)
                    session.add(row)
                    session.commit()
            console.print(f"  [dim]No new sources for {article.title}, cleared flag.[/dim]")
            continue

        # Map new sources to the article's topic
        try:
            extractions = map_chunks_to_topic(
                topic_query=article.title,
                scope="",
                domain=article.domain,
                model=model,
                key_source_ids=new_source_ids,
            )
        except Exception as exc:
            console.print(f"[red]  map_chunks_to_topic failed ({exc}), skipping[/red]")
            raise

        relevant = [e for e in extractions if e.is_relevant]
        if not relevant:
            console.print(f"  [dim]No relevant extractions for {article.title}.[/dim]")
        else:
            # Read body for contradiction detection
            text = art_path.read_text(encoding="utf-8", errors="replace")
            body = _strip_frontmatter(text)

            # Get domain persona
            try:
                persona = get_or_create_persona(article.domain, model=model)
            except Exception as exc:
                logger.warning("Could not fetch persona for %r: %s", article.domain, exc)
                persona = ""

            # Check any extraction for contradiction
            has_contradiction = any(detect_contradiction(body, e.extraction) for e in relevant)

            try:
                if has_contradiction:
                    updated_body = revisionary_update(art_path, relevant, persona, model)
                    revisionary_count += 1
                else:
                    updated_body = additive_update(art_path, relevant, persona, model)
                    additive_count += 1
            except Exception as exc:
                console.print(f"[red]  LLM update failed ({exc}), skipping[/red]")
                raise

            write_article(
                path=art_path,
                title=article.title,
                content=updated_body,
                sources=source_ids,
                topics=json.loads(article.topic_keys or "[]"),
                status=article.status,
                model=model or article.model,
            )

        # Mark article as synced
        with Session(engine) as session:
            row = session.exec(select(WikiArticle).where(WikiArticle.id == article.id)).first()
            if row is not None:
                row.needs_update = False
                row.updated_at = datetime.now(timezone.utc)
                session.add(row)
                session.commit()

        synced += 1

    generate_wiki_index(wiki_dir)
    console.print(
        f"[green]Synced {synced} articles "
        f"({revisionary_count} revisionary, {additive_count} additive)[/green]"
    )


@wiki_app.command("audit")
def wiki_audit(
    domain: str = typer.Option("", "--domain", "-d", help="Filter by domain"),
    fix: bool = typer.Option(False, "--fix", help="Queue split/merge candidates for sync"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
):
    """Report structural issues in the wiki (split/merge/orphan/contradiction/drift).

    Writes a full audit report to data/wiki/_audit.md.
    Use --fix to automatically queue split/merge candidates for sync.
    """
    from datetime import datetime, timezone

    from sqlmodel import Session, select

    from wikify.store.db import get_engine
    from wikify.store.models import WikiArticle
    from wikify.wiki.maintenance import structural_audit

    wiki_dir = Path("data/wiki")
    report = structural_audit(wiki_dir, domain=domain, model=model)

    # ── Print summary ─────────────────────────────────────────────────────────
    console.print("\n[bold]Wiki Structural Audit[/bold]")
    if domain:
        console.print(f"[dim]Domain: {domain}[/dim]\n")

    console.print(f"  Split candidates (>15 coverage rows): {len(report.split_candidates)}")
    for slug in report.split_candidates:
        console.print(f"    - {slug}")

    console.print(f"  Merge candidates (>80% source overlap): {len(report.merge_candidates)}")
    for a, b in report.merge_candidates:
        console.print(f"    - {a} <-> {b}")

    n_dep = len(report.deprecation_candidates)
    console.print(f"  Deprecation candidates (0 coverage, <3 sources): {n_dep}")
    for slug in report.deprecation_candidates:
        console.print(f"    - {slug}")

    console.print(f"  Orphan sources (no coverage anywhere): {len(report.orphan_sources)}")
    if report.orphan_sources:
        console.print("    [dim](showing first 10)[/dim]")
        for src in report.orphan_sources[:10]:
            console.print(f"    - {src}")

    console.print(f"  Contradiction flags (WARNING in body): {len(report.contradiction_flags)}")
    for slug in report.contradiction_flags:
        console.print(f"    - {slug}")

    console.print(f"  Graph drift (hub/bridge not in any article): {len(report.graph_drift)}")
    for name in report.graph_drift[:10]:
        console.print(f"    - {name}")

    # ── Write audit file ──────────────────────────────────────────────────────
    wiki_dir.mkdir(parents=True, exist_ok=True)
    audit_path = wiki_dir / "_audit.md"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# Wiki Structural Audit",
        "",
        f"_Generated: {now_str}_",
        f"_Domain filter: {domain or '(all)'}_",
        "",
        "## Split Candidates",
        "_Articles with >15 SourceCoverage rows (may need splitting into sub-articles)_",
        "",
    ]
    if report.split_candidates:
        for slug in report.split_candidates:
            lines.append(f"- {slug}")
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Merge Candidates",
        "_Article pairs with >80% overlapping source_ids (may cover duplicate territory)_",
        "",
    ]
    if report.merge_candidates:
        for a, b in report.merge_candidates:
            lines.append(f"- {a} <-> {b}")
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Deprecation Candidates",
        "_Articles with zero SourceCoverage rows and fewer than 3 source_ids_",
        "",
    ]
    if report.deprecation_candidates:
        for slug in report.deprecation_candidates:
            lines.append(f"- {slug}")
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Orphan Sources",
        "_Papers in the corpus that are not referenced in any wiki article_",
        "",
    ]
    if report.orphan_sources:
        for src in report.orphan_sources:
            lines.append(f"- {src}")
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Contradiction Flags",
        "_Articles containing WARNING markers (unresolved contradictions)_",
        "",
    ]
    if report.contradiction_flags:
        for slug in report.contradiction_flags:
            lines.append(f"- {slug}")
    else:
        lines.append("_None_")
    lines += [
        "",
        "## Graph Drift",
        "_Hub/bridge papers identified by graph analysis not yet referenced in any article_",
        "",
    ]
    if report.graph_drift:
        for name in report.graph_drift:
            lines.append(f"- {name}")
    else:
        lines.append("_None_")

    audit_path.write_text("\n".join(lines), encoding="utf-8")

    # ── Apply --fix ───────────────────────────────────────────────────────────
    if fix:
        fix_slugs: set[str] = set(report.split_candidates)
        for a, b in report.merge_candidates:
            fix_slugs.add(a)
            fix_slugs.add(b)

        if fix_slugs:
            engine = get_engine()
            with Session(engine) as session:
                for slug in fix_slugs:
                    row = session.exec(select(WikiArticle).where(WikiArticle.id == slug)).first()
                    if row is not None:
                        row.needs_update = True
                        session.add(row)
                session.commit()
            console.print(
                f"[green]Queued {len(fix_slugs)} article(s) for sync (needs_update=True)[/green]"
            )

    console.print(f"[green]Audit complete. Report saved to {audit_path}[/green]")


@wiki_app.command("health")
def wiki_health():
    """Report orphans, stale articles, and synthesis gaps in the wiki."""
    from datetime import datetime, timezone

    from sqlmodel import Session, select

    from wikify.store.db import get_engine
    from wikify.store.models import WikiArticle
    from wikify.wiki.builder import find_stale_articles, slugify

    wiki_dir = Path("data/wiki")

    engine = get_engine()
    try:
        with Session(engine) as session:
            all_articles = list(session.exec(select(WikiArticle)).all())
    except Exception:
        all_articles = []

    # Stale flag count
    needs_update = [a for a in all_articles if a.needs_update]

    # Find stale by age (older than 30 days)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    age_stale = find_stale_articles(list(all_articles), cutoff)

    # Orphaned: topic_keys is empty list
    orphans = [a for a in all_articles if a.topic_keys in ("[]", "", None)]

    # Missing: synthesis opportunities not yet in wiki
    missing: list[str] = []
    try:
        import re

        from wikify.papers.agent.tools import find_synthesis_opportunities

        opportunities = find_synthesis_opportunities()
        opp_concepts: list[str] = []
        for line in opportunities.splitlines():
            line = line.strip()
            if re.match(r"^(\d+\.|\-|\*)\s+", line):
                concept = re.sub(r"^(\d+\.|\-|\*)\s+", "", line).strip()
                if concept:
                    opp_concepts.append(concept)
        existing_slugs = {a.id for a in all_articles}
        missing = [c for c in opp_concepts if slugify(c) not in existing_slugs]
    except Exception:
        missing = []

    # Build health report
    lines: list[str] = [
        "# Wiki Health Report",
        "",
        f"- Total articles: {len(all_articles)}",
        f"- Needs update (flag): {len(needs_update)}",
        f"- Age-stale (>30 days): {len(age_stale)}",
        f"- Orphaned (empty topic_keys): {len(orphans)}",
        f"- Missing synthesis opportunities: {len(missing)}",
        "",
    ]

    if needs_update:
        lines += ["## Needs Update", ""]
        for a in needs_update:
            lines.append(f"- {a.title} (status: {a.status})")
        lines.append("")

    if orphans:
        lines += ["## Orphaned Articles", ""]
        for a in orphans:
            lines.append(f"- {a.title} ({a.file_path})")
        lines.append("")

    if missing:
        lines += ["## Missing Articles (synthesis opportunities)", ""]
        for c in missing[:20]:
            lines.append(f"- {c}")
        lines.append("")

    report = "\n".join(lines)

    health_path = wiki_dir / "_health.md"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    health_path.write_text(report, encoding="utf-8")

    console.print(report)
    console.print(f"[dim]Saved: {health_path}[/dim]")


def _answer_with_escalation(
    question: str,
    wiki_dir: Path,
    domain: str,
    model: str | None,
) -> str | None:
    """Run the 5-level escalation protocol to answer a wiki question.

    Level 0: Read _index.md -- can this be answered from domain/theme info?
    Level 1: Read relevant domain _index.md + theme index(es).
    Level 2: Read the specific article file(s) identified.
    Level 3: Read source digests cited in article Source Pointers.
    Level 4: Read source sections named in Source Pointers.
    If still unanswered after Level 4: record gap and return None.

    Returns:
        The answer string if answered at any level; None if unanswered.
    """
    import re

    from wikify.papers.agent.tools import read_paper_digest, read_section
    from wikify.llm.client import complete

    decision_prompt_template = (
        "Question: {question}\n\n"
        "Content at hand:\n{content}\n\n"
        "Can you answer this question fully and accurately from what you have read?\n"
        "Respond with either:\n"
        "ANSWER: [your complete answer]\n"
        "or\n"
        "ESCALATE: [exactly what information is missing and what source/section would contain it]"
    )

    def _llm_decide(content: str) -> tuple[bool, str]:
        """Call LLM with escalation decision prompt. Returns (answered, text)."""
        resp = complete(
            messages=[
                {
                    "role": "user",
                    "content": decision_prompt_template.format(question=question, content=content),
                }
            ],
            model=model,
            temperature=0.1,
            max_tokens=1500,
            use_cache=False,
        )
        resp = resp.strip()
        if resp.startswith("ANSWER:"):
            return True, resp[len("ANSWER:") :].strip()
        return False, resp[len("ESCALATE:") :].strip() if resp.startswith("ESCALATE:") else resp

    # ── Level 0: global index ─────────────────────────────────────────────────
    index_path = wiki_dir / "_index.md"
    if index_path.exists():
        level0_content = index_path.read_text(encoding="utf-8")
        answered, text = _llm_decide(level0_content)
        if answered:
            return text
        logger.debug("escalation level 0 -> escalate: %s", text[:120])

    # ── Level 1: domain index ─────────────────────────────────────────────────
    domain_index_paths: list[Path] = []
    if domain:
        di = wiki_dir / "domains" / domain / "_index.md"
        if di.exists():
            domain_index_paths.append(di)
    else:
        # Try to find any matching domain _index
        for di in wiki_dir.glob("domains/*/_index.md"):
            domain_index_paths.append(di)

    level1_texts = [p.read_text(encoding="utf-8") for p in domain_index_paths[:2]]
    # Also add theme indexes
    for theme_idx in list(wiki_dir.glob("domains/**/_index_*.md"))[:3]:
        level1_texts.append(theme_idx.read_text(encoding="utf-8"))

    if level1_texts:
        answered, text = _llm_decide("\n\n---\n\n".join(level1_texts))
        if answered:
            return text
        logger.debug("escalation level 1 -> escalate: %s", text[:120])

    # ── Level 2: find and read specific articles ───────────────────────────────
    # Use LLM to identify which article(s) to read from the index
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    nav_resp = complete(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a wiki navigator. Identify the 2-3 most relevant article "
                    "filenames (slugs) from the wiki index. "
                    "Return only the slugs, one per line, no extension, no explanation."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Wiki index:\n\n{index_text}\n\n"
                    f"Question: {question}\n\n"
                    "Return the 2-3 most relevant article slugs, one per line."
                ),
            },
        ],
        model=model,
        temperature=0.1,
        max_tokens=200,
        use_cache=False,
    )
    candidate_slugs = [
        line.strip().strip("-").strip()
        for line in nav_resp.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    article_texts: list[str] = []
    article_files: list[Path] = []
    for slug in candidate_slugs[:3]:
        for md_file in wiki_dir.rglob(f"{slug}.md"):
            if not md_file.name.startswith("_"):
                article_texts.append(md_file.read_text(encoding="utf-8"))
                article_files.append(md_file)
                break

    if not article_texts:
        for md_file in sorted(wiki_dir.rglob("*.md"))[:3]:
            if not md_file.name.startswith("_"):
                article_texts.append(md_file.read_text(encoding="utf-8"))
                article_files.append(md_file)

    if article_texts:
        answered, text = _llm_decide("\n\n---\n\n".join(article_texts))
        if answered:
            return text
        logger.debug("escalation level 2 -> escalate: %s", text[:120])

    # ── Level 3: read source digests from Source Pointers ────────────────────
    digest_texts: list[str] = []
    source_pointer_pattern = re.compile(r"\[REF:([^\]]+)\]")
    for art_text in article_texts:
        # Find Source Pointers section
        sp_match = re.search(
            r"##\s+Source Pointers\s*\n(.*?)(?=\n##\s|\Z)", art_text, re.DOTALL | re.IGNORECASE
        )
        if sp_match:
            sp_section = sp_match.group(1)
            display_names = source_pointer_pattern.findall(sp_section)
            for name in display_names[:5]:
                digest = read_paper_digest(
                    name[:16], reason=f"wiki query escalation L3: {question[:60]}"
                )
                if digest:
                    digest_texts.append(f"[Source: {name}]\n{digest}")

    if digest_texts:
        answered, text = _llm_decide("\n\n---\n\n".join(digest_texts))
        if answered:
            return text
        logger.debug("escalation level 3 -> escalate: %s", text[:120])

    # ── Level 4: read source sections ────────────────────────────────────────
    section_texts: list[str] = []
    # Parse "source - section" style from escalation text at level 3
    for art_text in article_texts:
        sp_match = re.search(
            r"##\s+Source Pointers\s*\n(.*?)(?=\n##\s|\Z)", art_text, re.DOTALL | re.IGNORECASE
        )
        if sp_match:
            sp_section = sp_match.group(1)
            # Look for lines like: "Smith 2021 - Results: [description]"
            for line in sp_section.splitlines():
                line = line.strip()
                if " - " in line:
                    parts = line.split(" - ", 1)
                    source_pat = parts[0].strip().strip("*-[] ")
                    section_hint = parts[1].split(":")[0].strip() if ":" in parts[1] else "results"
                    sec = read_section(
                        source_pat[:16],
                        section_hint,
                        reason=f"wiki query escalation L4: {question[:60]}",
                    )
                    if sec:
                        section_texts.append(f"[Source: {source_pat} / {section_hint}]\n{sec}")
            if section_texts:
                break  # enough content for one LLM call

    if section_texts:
        answered, text = _llm_decide("\n\n---\n\n".join(section_texts))
        if answered:
            return text
        logger.debug("escalation level 4 -> unanswered")

    # Unanswered after all levels
    return None


@wiki_app.command("query")
def wiki_query(
    question: str = typer.Argument(..., help="Question to answer from the wiki"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
    domain: str = typer.Option("", "--domain", "-d", help="Limit search to a domain"),
    deep: bool = typer.Option(False, "--deep", help="Build ephemeral mini-wiki before escalation"),
    promote: bool = typer.Option(False, "--promote", help="Save the answer as a new wiki article"),
):
    """Answer a question from the visible wiki with optional promotion."""
    import tempfile

    from wikify.wiki.presentation.layout import iter_visible_page_files
    from wikify.wiki.runtime import query_wiki, reconcile_state

    wiki_dir = Path("data/wiki")
    index_path = wiki_dir / "index.md"
    legacy_index_path = wiki_dir / "_index.md"

    if not index_path.exists() and not legacy_index_path.exists() and not iter_visible_page_files(
        wiki_dir
    ):
        console.print("[red]No visible wiki found. Run 'wikify wiki epoch' first.[/red]")
        raise typer.Exit(1)

    # ── --deep mode: build ephemeral mini-wiki ────────────────────────────────
    query_wiki_dir = wiki_dir
    if deep:
        console.print("[dim]--deep: building ephemeral mini-wiki for this query...[/dim]")
        try:
            from wikify.wiki.legacy.agent import build_wiki_from_sitemap
            from wikify.wiki.builder import generate_wiki_index
            from wikify.wiki.legacy.sitemap import generate_sitemap

            temp_dir_obj = tempfile.mkdtemp()
            temp_wiki_dir = Path(str(temp_dir_obj))
            sitemap = generate_sitemap(
                wiki_dir=temp_wiki_dir,
                topic_hint=question,
                max_explore_papers=15,
                model=model,
            )
            build_wiki_from_sitemap(sitemap, wiki_dir=temp_wiki_dir, model=model)
            generate_wiki_index(temp_wiki_dir)
            query_wiki_dir = temp_wiki_dir
            console.print(f"[dim]Ephemeral wiki built in {temp_wiki_dir}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]--deep build failed ({exc}), using existing wiki.[/yellow]")

    # ── Run escalation ────────────────────────────────────────────────────────
    if (
        not deep
        and query_wiki_dir == wiki_dir
        and legacy_index_path.exists()
        and not iter_visible_page_files(query_wiki_dir)
    ):
        from wikify.wiki.builder import append_unanswered_question

        answer = _answer_with_escalation(question, query_wiki_dir, domain, model)
        if answer is None:
            append_unanswered_question(wiki_dir, question, domain)
            console.print("[yellow]Gap recorded in wiki. Run 'wiki expand' to address.[/yellow]")
            return
        console.print(answer)
        return

    result = query_wiki(
        question,
        wiki_dir=query_wiki_dir,
        domain=domain,
        model=model,
        promote=promote,
        page_type="query",
        promotion_wiki_dir=wiki_dir,
    )
    answer = str(result.get("answer", "")).strip()

    if not result.get("answered"):
        console.print("[yellow]Gap recorded in wiki. Run 'wiki expand' to address.[/yellow]")
        return

    console.print(answer)

    # ── --promote (non-deep path) ─────────────────────────────────────────────
    if promote and result.get("promoted_path"):
        reconcile_state(wiki_dir)
        console.print(f"[green]Answer promoted to: {result['promoted_path']}[/green]")


@wiki_app.command("campaign")
def wiki_campaign(
    thesis: str = typer.Argument(..., help="Research thesis or guiding question"),
    name: str = typer.Option("", "--name", help="Optional campaign name"),
    domain: str = typer.Option("", "--domain", "-d", help="Limit campaign to one domain"),
    epochs: int = typer.Option(1, "--epochs", min=1, help="Number of epochs to run first"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model override"),
    promote: bool = typer.Option(
        True,
        "--promote/--no-promote",
        help="Promote the final campaign answer back into the visible wiki",
    ),
):
    """Run a thesis-driven campaign over the shared wiki runtime."""
    from wikify.wiki.runtime import run_campaign

    result = run_campaign(
        thesis,
        wiki_dir=Path("data/wiki"),
        name=name,
        domain=domain,
        epochs=epochs,
        model=model,
        promote=promote,
    )
    console.print("\n[bold]Wiki Campaign[/bold]")
    console.print(f"  Campaign id       : {result.get('campaign_id', '')}")
    console.print(f"  Epochs run        : {result.get('epochs_run', 0)}")
    console.print(f"  Answered          : {result.get('answered', False)}")
    if result.get("promoted_path"):
        console.print(f"  Promoted path     : {result.get('promoted_path', '')}")
    answer = str(result.get("answer", "")).strip()
    if answer:
        console.print("")
        console.print(answer)


@wiki_app.command("maintain")
def wiki_maintain():
    """Run a maintenance sweep over the visible wiki and operational state."""
    from wikify.wiki.runtime import run_maintain

    summary = run_maintain(Path("data/wiki"))
    console.print("\n[bold]Wiki Maintain[/bold]")
    console.print(f"  Pages seen        : {summary.get('pages_seen', 0)}")
    console.print(f"  Findings          : {summary.get('findings', 0)}")
    console.print(f"  Pages reconciled  : {summary.get('pages_updated', 0)}")
    console.print(f"  Pages created     : {summary.get('pages_created', 0)}")
    console.print(f"  Pages deleted     : {summary.get('pages_deleted', 0)}")


@wiki_app.command("reconcile-state")
def wiki_reconcile_state():
    """Rebuild operational page state from visible markdown files."""
    from wikify.wiki.runtime import reconcile_state

    summary = reconcile_state(Path("data/wiki"))
    console.print("\n[bold]Reconcile State[/bold]")
    console.print(f"  Pages seen        : {summary.get('pages_seen', 0)}")
    console.print(f"  Pages created     : {summary.get('pages_created', 0)}")
    console.print(f"  Pages updated     : {summary.get('pages_updated', 0)}")
    console.print(f"  Pages deleted     : {summary.get('pages_deleted', 0)}")


@wiki_app.command("export-metrics")
def wiki_export_metrics(
    workflow_type: str = typer.Option("", "--workflow", help="Optional workflow filter"),
    limit: int = typer.Option(20, "--limit", help="Maximum number of runs to export"),
):
    """Export aggregated telemetry and wiki metrics to data/wiki/_meta/metrics/export.json."""
    from wikify.wiki.runtime import export_metrics

    payload = export_metrics(Path("data/wiki"), workflow_type=workflow_type, limit=limit)
    console.print("\n[bold]Export Metrics[/bold]")
    console.print(f"  Runs exported     : {payload.get('run_count', 0)}")
    console.print(f"  Output            : {payload.get('export_path', '')}")


@wiki_app.command("compare-runs")
def wiki_compare_runs(
    workflow_type: str = typer.Option("", "--workflow", help="Optional workflow filter"),
    limit: int = typer.Option(10, "--limit", help="Maximum number of runs to compare"),
):
    """Compare recent runs on cost, retrieval effort, and wiki outcome metrics."""
    from wikify.wiki.runtime import compare_runs

    payload = compare_runs(Path("data/wiki"), workflow_type=workflow_type, limit=limit)
    console.print("\n[bold]Compare Runs[/bold]")
    console.print(f"  Runs compared     : {payload.get('run_count', 0)}")
    for row in payload.get("runs", [])[:5]:
        console.print(
            "  "
            f"{row.get('run_id', '')}: "
            f"tokens={row.get('total_tokens', 0)} "
            f"pages={row.get('pages_touched', 0)} "
            f"orphans={row.get('orphan_count', 0)} "
            f"evidence={row.get('evidence_density', 0.0):.2f}"
        )


@wiki_app.command("epoch")
def wiki_epoch(
    n: int = typer.Option(1, "--n", help="Number of epochs to run"),
    until_convergence: bool = typer.Option(
        False, "--until-convergence", help="Run until convergence"
    ),
    status: bool = typer.Option(False, "--status", help="Show current epoch status"),
    domain: str = typer.Option("", "--domain", help="Restrict to one domain"),
    on_ingest: bool = typer.Option(False, "--on-ingest", help="Configure auto-trigger on ingest"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model override"),
):
    """Run one or more wiki-building epochs.

    Passes: discovery -> graph -> articles -> cross-ref -> index.
    """
    import json

    from wikify.wiki.epoch import get_epoch_status, run_epoch, run_until_convergence

    wiki_dir = Path("data/wiki")

    if status:
        s = get_epoch_status()
        console.print("\n[bold]Epoch Status[/bold]")
        console.print(f"  Epochs completed : {s.get('epochs_completed', 0)}")
        console.print(f"  Loss (L)         : {s.get('loss', 'n/a')}")
        console.print(f"  Converged        : {s.get('converged', False)}")
        console.print(f"  Last run         : {s.get('last_run', 'never')}")
        return

    if on_ingest:
        flag_path = wiki_dir / "_epoch.json"
        wiki_dir.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(json.dumps({"on_ingest": True}))
        console.print(f"[green]Auto-trigger on ingest enabled.[/green] Flag: {flag_path}")
        return

    if until_convergence:
        max_epochs = n if n > 1 else 10
        logs = run_until_convergence(domain=domain, max_epochs=max_epochs, model=model)
        epochs_run = len(logs)
        final_log = logs[-1] if logs else None
        console.print(f"\n[bold green]Converged after {epochs_run} epoch(s)[/bold green]")
        console.print(
            f"  Final loss : {f'{final_log.loss_score:.4f}' if final_log is not None else 'n/a'}"
        )
        if final_log is not None:
            console.print(f"  Articles   : {final_log.articles_written}")
            console.print(f"  Upgrades   : {final_log.stubs_upgraded}")
        return

    for i in range(n):
        console.print(f"\n[bold]Epoch {i + 1}/{n}[/bold]")
        result = run_epoch(triggered_by="user", domain=domain, model=model)
        console.print(f"  Concepts discovered : {result.concepts_discovered}")
        console.print(f"  Articles written    : {result.articles_written}")
        console.print(f"  Stubs upgraded      : {result.stubs_upgraded}")
        loss = result.loss_score
        loss_delta = result.loss_delta
        loss_str = f"{loss:.4f}" if loss is not None else "n/a"
        delta_str = f"{loss_delta:+.4f}" if loss_delta is not None else "n/a"
        console.print(f"  Loss (L)            : {loss_str}  (delta: {delta_str})")
        converged = result.converged
        converged_label = "[green]yes[/green]" if converged else "no"
        console.print(f"  Converged           : {converged_label}")
        if converged:
            console.print("[green]Wiki has converged — no further epochs needed.[/green]")
            break


@wiki_app.command("dashboard")
def wiki_dashboard(
    port: int = typer.Option(8765, "--port", help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    """Serve the live wiki dashboard (requires uvicorn + wikify.wiki.dashboard)."""
    import uvicorn

    from wikify.wiki.presentation.dashboard import app as dashboard_app

    console.print(f"[bold]Serving wiki dashboard at[/bold] http://{host}:{port}")
    uvicorn.run(dashboard_app, host=host, port=port)


@wiki_app.command("html")
def wiki_html(
    serve: bool = typer.Option(False, "--serve", help="Serve after building"),
    port: int = typer.Option(8080, "--port", help="Port for local server"),
    wiki_dir: str = typer.Option("data/wiki", "--wiki-dir", help="Wiki source directory"),
    output_dir: str = typer.Option(
        "", "--output", "-o", help="Output directory (default: wiki_dir/_site)"
    ),
):
    """Build Wikipedia-style HTML site from wiki."""
    from wikify.wiki.presentation.html import build_site, serve_site

    src = Path(wiki_dir)
    if not src.exists():
        console.print(f"[red]Wiki directory not found:[/red] {wiki_dir}")
        raise typer.Exit(1)

    out = Path(output_dir) if output_dir else None
    console.print(f"[bold]Building HTML site from[/bold] {src}")
    site_path = build_site(src, out)
    console.print(f"[green]Site built at {site_path}[/green]")

    if serve:
        serve_site(site_path, port=port)


@wiki_app.command("migrate-figures")
def wiki_migrate_figures(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be moved without moving"),
):
    """Reorganize figures from hash-based dirs to per-paper folders.

    Moves figures from data/figures/{hash[:2]}/{hash[2:4]}/{hash}.{ext}
    to data/figures/{paper_slug}/{figure_label}.{ext} and updates the DB.
    """
    import re
    import shutil
    import sqlite3

    from wikify.config import settings
    from wikify.extract.media import _make_figure_filename, _make_paper_slug

    db_path = settings.db_path
    figures_dir = settings.figures_dir

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()

    # Get all figures with their paper source_path
    c.execute("""
        SELECT f.id, f.figure_number, f.format, f.image_path, p.source_path, p.title
        FROM figure f JOIN paper p ON f.paper_id = p.id
    """)
    rows = c.fetchall()
    console.print(f"Found [bold]{len(rows)}[/bold] figures to migrate")

    moved = 0
    skipped = 0
    for fig_id, fig_number, fmt, old_path_str, source_path, title in rows:
        ext = fmt or "png"
        # Derive paper slug from source PDF path (or title as fallback)
        if source_path:
            paper_slug = _make_paper_slug(source_path)
        elif title:
            slug = re.sub(r"[^\w\s-]", "", title)
            slug = re.sub(r"[\s]+", "_", slug).strip("_")
            paper_slug = slug[:80]
        else:
            paper_slug = fig_id[:16]

        fig_filename = _make_figure_filename(fig_number or f"img_{fig_id[:8]}", ext)
        new_dir = figures_dir / paper_slug
        new_path = new_dir / fig_filename

        # Resolve old file location
        old_path = Path(old_path_str) if old_path_str else None
        if old_path and not old_path.is_absolute():
            old_path = Path.cwd() / old_path
        if old_path is None or not old_path.exists():
            # Try content-addressed fallback
            old_path = figures_dir / fig_id[:2] / fig_id[2:4] / f"{fig_id}.{ext}"

        if not old_path.exists():
            skipped += 1
            continue

        # Already in new location?
        if old_path == new_path or str(new_path) in old_path_str:
            skipped += 1
            continue

        if dry_run:
            console.print(
                f"  [dim]{old_path.name}[/dim] -> [bold]{paper_slug}/{fig_filename}[/bold]",
                highlight=False,
            )
            moved += 1
            continue

        new_dir.mkdir(parents=True, exist_ok=True)

        # Handle collisions
        if new_path.exists():
            import hashlib

            existing_hash = hashlib.sha256(new_path.read_bytes()).hexdigest()
            if existing_hash == fig_id:
                # Same content, just update DB
                pass
            else:
                stem = new_path.stem
                new_path = new_dir / f"{stem}_{fig_id[:8]}.{ext}"

        if not new_path.exists():
            shutil.copy2(str(old_path), str(new_path))

        # Copy sidecar if exists
        meta_old = old_path.with_suffix(f".{ext}.meta.json")
        if meta_old.exists():
            meta_new = new_path.with_suffix(f".{ext}.meta.json")
            if not meta_new.exists():
                shutil.copy2(str(meta_old), str(meta_new))

        # Update DB
        c.execute(
            "UPDATE figure SET image_path = ? WHERE id = ?",
            (str(new_path), fig_id),
        )
        moved += 1

    if not dry_run:
        conn.commit()

    conn.close()

    action = "Would move" if dry_run else "Moved"
    console.print(
        f"[green]{action} {moved} figures[/green], skipped {skipped}"
    )

    if not dry_run and moved > 0:
        console.print(
            "[dim]Old hash-based directories can be removed manually after verifying.[/dim]"
        )


if __name__ == "__main__":
    app()
