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
        from scholarforge.evaluate.pi_review import evaluate_pi, parse_pi_review

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
    from pathlib import Path

    from scholarforge.agent.revision import revise_weakest_section
    from scholarforge.evaluate.pi_review import evaluate_pi, parse_pi_review

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


# ── Wiki commands ─────────────────────────────────────────────────────────────

wiki_app = typer.Typer(name="wiki", help="Build and maintain the curated wiki layer.")
app.add_typer(wiki_app)


@wiki_app.command("init")
def wiki_init(
    top_n: int = typer.Option(20, "--top-n", help="Number of concepts to bootstrap"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
):
    """Bootstrap wiki stubs from top-N concepts in the corpus."""
    import json

    from scholarforge.agent.tools import find_synthesis_opportunities, get_corpus_summary
    from scholarforge.config import settings
    from scholarforge.store.db import get_session
    from scholarforge.store.models import WikiArticle
    from scholarforge.wiki.agent import build_wiki_article
    from scholarforge.wiki.builder import article_path, generate_wiki_index, slugify, write_article

    wiki_dir = settings.data_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    console.print("[bold]Bootstrapping wiki...[/bold]")
    summary = get_corpus_summary()
    opportunities = find_synthesis_opportunities()
    console.print(f"[dim]Corpus summary: {len(summary)} chars[/dim]")

    # Extract concept titles from synthesis opportunities (one per line starting with a number or -)
    import re

    concept_lines: list[str] = []
    for line in opportunities.splitlines():
        line = line.strip()
        if re.match(r"^(\d+\.|\-|\*)\s+", line):
            concept = re.sub(r"^(\d+\.|\-|\*)\s+", "", line).strip()
            if concept:
                concept_lines.append(concept)

    concepts = concept_lines[:top_n]
    if not concepts:
        console.print("[yellow]No synthesis opportunities found. Check corpus size.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold]Creating {len(concepts)} stubs...[/bold]")
    created = 0

    with get_session() as session:
        for concept in concepts:
            slug = slugify(concept)
            path = article_path(wiki_dir, "concepts", slug)
            rel_path = str(path.relative_to(settings.data_dir))

            existing = session.get(WikiArticle, slug)
            if existing:
                console.print(f"[dim]Already exists: {slug}[/dim]")
                continue

            console.print(f"  Stub: {concept}")
            try:
                content, source_ids = build_wiki_article(
                    concept, concept, status="stub", model=model, top_k=5
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]  Failed ({exc})[/red]")
                continue

            write_article(
                path=path,
                title=concept,
                content=content,
                sources=source_ids,
                topics=[concept],
                status="stub",
                model=model or "",
            )

            row = WikiArticle(
                id=slug,
                title=concept,
                status="stub",
                file_path=rel_path,
                source_ids=json.dumps(source_ids),
                topic_keys=json.dumps([concept]),
                model=model or "",
            )
            session.add(row)
            created += 1

        session.commit()

    # Write index
    index_path = wiki_dir / "_index.md"
    index_path.write_text(generate_wiki_index(wiki_dir), encoding="utf-8")

    console.print(f"[green]Created {created} stubs. Index: {index_path}[/green]")


@wiki_app.command("expand")
def wiki_expand(
    concept: str = typer.Argument(
        "", help="Concept slug or title to expand (empty = expand all stubs)"
    ),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
):
    """Expand stub article(s) to full articles."""
    import json

    from sqlmodel import select

    from scholarforge.config import settings
    from scholarforge.store.db import get_session
    from scholarforge.store.models import WikiArticle
    from scholarforge.wiki.agent import build_wiki_article
    from scholarforge.wiki.builder import generate_wiki_index, write_article

    wiki_dir = settings.data_dir / "wiki"

    with get_session() as session:
        if concept:
            # Find by slug or title match
            from scholarforge.wiki.builder import slugify

            slug = slugify(concept)
            article = session.get(WikiArticle, slug)
            if not article:
                # Try title match
                all_articles = session.exec(select(WikiArticle)).all()
                matches = [a for a in all_articles if concept.lower() in a.title.lower()]
                if not matches:
                    console.print(f"[red]No wiki article found for: {concept}[/red]")
                    raise typer.Exit(1)
                article = matches[0]
            targets = [article]
        else:
            targets = list(
                session.exec(select(WikiArticle).where(WikiArticle.status == "stub")).all()
            )

        if not targets:
            console.print("[yellow]No stubs to expand.[/yellow]")
            return

        console.print(f"[bold]Expanding {len(targets)} article(s)...[/bold]")

        for article in targets:
            console.print(f"  Expanding: {article.title}")
            try:
                content, source_ids = build_wiki_article(
                    article.title, article.title, status="full", model=model, top_k=8
                )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]  Failed ({exc})[/red]")
                continue

            path = settings.data_dir / article.file_path
            existing_sources = json.loads(article.source_ids or "[]")
            all_sources = list(dict.fromkeys(existing_sources + source_ids))

            write_article(
                path=path,
                title=article.title,
                content=content,
                sources=all_sources,
                topics=json.loads(article.topic_keys or "[]"),
                status="full",
                model=model or "",
            )

            from datetime import datetime, timezone

            article.status = "full"
            article.source_ids = json.dumps(all_sources)
            article.model = model or article.model
            article.updated_at = datetime.now(timezone.utc)
            session.add(article)

        session.commit()

    # Regenerate index
    index_path = wiki_dir / "_index.md"
    index_path.write_text(generate_wiki_index(wiki_dir), encoding="utf-8")
    console.print(f"[green]Expansion complete. Index updated: {index_path}[/green]")


@wiki_app.command("sync")
def wiki_sync(
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
):
    """Update stale wiki articles (needs_update=True) with new corpus evidence."""
    import json
    from datetime import datetime, timezone

    from sqlmodel import select

    from scholarforge.agent.tools import read_paper_digest
    from scholarforge.config import settings
    from scholarforge.store.db import get_session
    from scholarforge.store.models import WikiArticle
    from scholarforge.wiki.agent import update_wiki_article
    from scholarforge.wiki.builder import generate_wiki_index, write_article

    wiki_dir = settings.data_dir / "wiki"

    with get_session() as session:
        stale = list(session.exec(select(WikiArticle).where(WikiArticle.needs_update)).all())

        if not stale:
            console.print("[green]All wiki articles are up to date.[/green]")
            return

        console.print(f"[bold]Syncing {len(stale)} stale article(s)...[/bold]")

        for article in stale:
            console.print(f"  Syncing: {article.title}")
            path = settings.data_dir / article.file_path
            if not path.exists():
                console.print(f"[yellow]  File missing, skipping: {path}[/yellow]")
                continue

            # Read current content (strip frontmatter)
            text = path.read_text(encoding="utf-8", errors="replace")
            # Find end of frontmatter block
            parts = text.split("---\n", 2)
            body = parts[2].strip() if len(parts) >= 3 else text

            # Fetch digests for known source IDs
            source_ids = json.loads(article.source_ids or "[]")
            digests: list[str] = []
            for pid in source_ids[:5]:
                try:
                    digest = read_paper_digest(pid[:16], reason=f"wiki sync: {article.title}")
                    if digest:
                        digests.append(digest)
                except Exception:  # noqa: BLE001
                    pass

            try:
                revised_body = update_wiki_article(body, digests, model=model)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]  LLM update failed ({exc}), skipping[/red]")
                continue

            write_article(
                path=path,
                title=article.title,
                content=revised_body,
                sources=source_ids,
                topics=json.loads(article.topic_keys or "[]"),
                status=article.status,
                model=model or article.model,
            )

            article.needs_update = False
            article.updated_at = datetime.now(timezone.utc)
            session.add(article)

        session.commit()

    index_path = wiki_dir / "_index.md"
    index_path.write_text(generate_wiki_index(wiki_dir), encoding="utf-8")
    console.print(f"[green]Sync complete. Index updated: {index_path}[/green]")


@wiki_app.command("health")
def wiki_health():
    """Report orphans, stale articles, and synthesis gaps."""
    from datetime import datetime, timezone

    from sqlmodel import select

    from scholarforge.agent.tools import find_synthesis_opportunities
    from scholarforge.config import settings
    from scholarforge.store.db import get_session
    from scholarforge.store.models import WikiArticle
    from scholarforge.wiki.builder import find_stale_articles, slugify

    wiki_dir = settings.data_dir / "wiki"

    with get_session() as session:
        all_articles = session.exec(select(WikiArticle)).all()

    # Stale count
    needs_update = [a for a in all_articles if a.needs_update]

    # Find stale by age (older than 30 days)
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    age_stale = find_stale_articles(list(all_articles), cutoff)

    # Orphaned: file path doesn't exist on disk
    orphans = [a for a in all_articles if not (settings.data_dir / a.file_path).exists()]

    # Missing: synthesis opportunities not yet in wiki
    try:
        opportunities = find_synthesis_opportunities()
        import re

        opp_concepts: list[str] = []
        for line in opportunities.splitlines():
            line = line.strip()
            if re.match(r"^(\d+\.|\-|\*)\s+", line):
                concept = re.sub(r"^(\d+\.|\-|\*)\s+", "", line).strip()
                if concept:
                    opp_concepts.append(concept)
        existing_slugs = {a.id for a in all_articles}
        missing = [c for c in opp_concepts if slugify(c) not in existing_slugs]
    except Exception:  # noqa: BLE001
        missing = []

    # Build health report
    lines: list[str] = [
        "# Wiki Health Report",
        "",
        f"- Total articles: {len(all_articles)}",
        f"- Needs update (flag): {len(needs_update)}",
        f"- Age-stale (>30 days): {len(age_stale)}",
        f"- Orphaned (file missing): {len(orphans)}",
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


if __name__ == "__main__":
    app()
