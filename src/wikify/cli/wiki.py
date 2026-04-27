"""``wikify wiki ...`` — committed wiki layer for wiki bundles.

Subcommands::

    wiki list [articles|people|files] [--run]
    wiki find "<query>" [--run] [--text]
    wiki show <handle> [--run] [--full]
    wiki build indexes [--run]
    wiki check [--run]
    wiki commit <concept> [--run] [--ensure-projections]
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle
from ..bundle.run.lock import LockHeldError
from ..bundle.wiki.commit import CommitGateError, commit_page
from ..bundle.wiki.derived import rebuild_graph, rebuild_index, rebuild_vectors
from ..bundle.wiki.queries import (
    find_text,
    list_articles,
    list_files,
    list_people,
    show_page,
)
from ._helpers import EXIT_LOCK_HELD, EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Committed wiki layer.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except FileNotFoundError as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except FileNotFoundError as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=f"no bundle resolved (cwd={cwd}); pass --run <bundle>. cause: {exc}",
        )


# --------------------------------------------------------------- list


list_app = typer.Typer(add_completion=False, help="List wiki handles.")
app.add_typer(list_app, name="list")


@list_app.callback(invoke_without_command=True)
def cmd_list_default(
    ctx: typer.Context,
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    bundle = _resolve_bundle(run)
    items = []
    for slug in list_articles(bundle):
        items.append({"slug": slug, "kind": "article"})
    for slug in list_people(bundle):
        items.append({"slug": slug, "kind": "person"})
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for it in items:
        typer.echo(f"{it['kind']:<8}  {it['slug']}")


@list_app.command("articles")
def cmd_list_articles(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    items = list_articles(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in items:
        typer.echo(s)


@list_app.command("people")
def cmd_list_people(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    items = list_people(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": items}))
        return
    for s in items:
        typer.echo(s)


@list_app.command("files")
def cmd_list_files(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    files = list_files(bundle)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": files}))
        return
    for f in files:
        typer.echo(f)


# --------------------------------------------------------------- find


@app.command("find")
def cmd_find(
    query: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    top_k: int = typer.Option(20, "--top-k"),
    text: bool = typer.Option(False, "--text"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Substring grep over committed pages (text mode is the only mode today)."""
    bundle = _resolve_bundle(run)
    if not text:
        # Default to text mode; graph + vector queries are a follow-up.
        text = True
    hits = find_text(bundle, query, top_k=top_k)
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "items": hits}))
        return
    for h in hits:
        typer.echo(f"{h['kind']:<8}  {h['slug']:<32}  {h['snippet']}")


# --------------------------------------------------------------- show


@app.command("show")
def cmd_show(
    handle: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    bundle = _resolve_bundle(run)
    info = show_page(bundle, handle=handle)
    if info is None:
        cli_error(EXIT_VALIDATION, error="page_not_found", handle=handle)
    if fmt == "json":
        body_payload = info["text"] if full else info["text"][:500]
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "slug": info["slug"],
                    "kind": info["kind"],
                    "path": info["path"],
                    "text": body_payload,
                }
            )
        )
        return
    typer.echo(f"slug:  {info['slug']}")
    typer.echo(f"kind:  {info['kind']}")
    typer.echo(f"path:  {info['path']}")
    typer.echo("---")
    typer.echo(info["text"] if full else info["text"][:500])


# --------------------------------------------------------------- build


@app.command("build")
def cmd_build(
    kind: str = typer.Argument(..., help="indexes | graph | vectors"),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Rebuild a derived projection.

    Three kinds:

    - ``indexes``  — derived/index.json (page list)
    - ``graph``    — derived/graph.json (cite-edge wiki graph)
    - ``vectors``  — derived/vectors.npz (per-page embeddings)
    """
    bundle = _resolve_bundle(run)
    if kind == "indexes":
        p = rebuild_index(bundle)
    elif kind == "graph":
        p = rebuild_graph(bundle)
    elif kind == "vectors":
        p = rebuild_vectors(bundle)
    else:
        cli_error(
            EXIT_VALIDATION,
            error="bad_build_kind",
            message=f"`wiki build {kind}` not recognised; use indexes|graph|vectors",
        )
    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "path": str(p)}))
        return
    typer.echo(f"{kind}: {p}")


# --------------------------------------------------------------- check


@app.command("check")
def cmd_check(
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Sanity-check the committed wiki: page count, derived/index.json freshness."""
    bundle = _resolve_bundle(run)
    n_articles = len(list_articles(bundle))
    n_people = len(list_people(bundle))
    has_index = bundle.derived_index_path.exists()
    summary = {
        "ok": True,
        "articles": n_articles,
        "people": n_people,
        "has_derived_index": has_index,
    }
    if fmt == "json":
        typer.echo(json.dumps(summary))
        return
    typer.echo(f"articles:           {n_articles}")
    typer.echo(f"people:             {n_people}")
    typer.echo(f"derived/index.json: {has_index}")


# --------------------------------------------------------------- commit


@app.command("commit")
def cmd_commit(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    ensure_projections: bool = typer.Option(False, "--ensure-projections"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Promote a validated response to wiki/articles/<slug>.md or wiki/people/<slug>.md."""
    bundle = _resolve_bundle(run)
    try:
        result = commit_page(
            bundle, slug=concept, ensure_projections=ensure_projections
        )
    except CommitGateError as exc:
        cli_error(EXIT_VALIDATION, error="commit_gate", message=str(exc))
    except LockHeldError as exc:
        cli_error(
            EXIT_LOCK_HELD,
            error="lock_held",
            message=str(exc),
            owner=exc.owner,
            acquired_at=exc.acquired_at,
        )
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "page_id": result.page_id,
                    "kind": result.kind,
                    "slug": result.slug,
                    "path": str(result.page_path.relative_to(bundle.root)).replace("\\", "/"),
                }
            )
        )
        return
    typer.echo(f"committed {result.page_id} -> {result.page_path}")


__all__ = ["app"]
