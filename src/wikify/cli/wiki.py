"""Wiki sub-CLI: build, query, maintain, and present the wiki layer.

Mounted into the root ``wikify`` Typer app from ``wikify.cli``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from wikify.cli._helpers import console

logger = logging.getLogger(__name__)

wiki_app = typer.Typer(name="wiki", help="Build and maintain the curated wiki layer.")


# NOTE: the legacy `wiki init` and `wiki expand` commands and the
# `wiki query --deep` mini-wiki branch were removed when the
# sitemap-first build flow was deleted. Bootstrap a new wiki by running
# `wikify wiki epoch` against an ingested corpus instead.


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

    from wikify.core.store.db import get_engine
    from wikify.core.store.models import SourceCoverage, WikiArticle
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

    from wikify.core.store.db import get_engine
    from wikify.core.store.models import WikiArticle
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

    from wikify.core.store.db import get_engine
    from wikify.core.store.models import WikiArticle
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


@wiki_app.command("query")
def wiki_query(
    question: str = typer.Argument(..., help="Question to answer from the wiki"),
    model: str = typer.Option(None, "--model", "-m", help="LLM model"),
    domain: str = typer.Option("", "--domain", "-d", help="Limit search to a domain"),
    promote: bool = typer.Option(False, "--promote", help="Save the answer as a new wiki article"),
):
    """Answer a question from the visible wiki with optional promotion."""
    from wikify.wiki.presentation.layout import iter_visible_page_files
    from wikify.wiki.runtime import query_wiki, reconcile_state

    wiki_dir = Path("data/wiki")
    index_path = wiki_dir / "index.md"

    if not index_path.exists() and not iter_visible_page_files(wiki_dir):
        console.print("[red]No visible wiki found. Run 'wikify wiki epoch' first.[/red]")
        raise typer.Exit(1)

    result = query_wiki(
        question,
        wiki_dir=wiki_dir,
        domain=domain,
        model=model,
        promote=promote,
        page_type="query",
        promotion_wiki_dir=wiki_dir,
    )
    answer = str(result.get("answer", "")).strip()

    if not result.get("answered"):
        console.print("[yellow]Gap recorded in wiki.[/yellow]")
        return

    console.print(answer)

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
    allow_echo_extractor: bool = typer.Option(
        False,
        "--allow-echo-extractor",
        help="Allow EchoExtractor fallback when no extractor is wired (tests/dry-run).",
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
        allow_echo_extractor=allow_echo_extractor,
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
    allow_echo_extractor: bool = typer.Option(
        False,
        "--allow-echo-extractor",
        help="Allow EchoExtractor fallback when no extractor is wired (tests/dry-run).",
    ),
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
        logs = run_until_convergence(
            domain=domain,
            max_epochs=max_epochs,
            model=model,
            allow_echo_extractor=allow_echo_extractor,
        )
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
        result = run_epoch(
            triggered_by="user",
            domain=domain,
            model=model,
            allow_echo_extractor=allow_echo_extractor,
        )
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
