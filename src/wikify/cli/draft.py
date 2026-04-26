"""``wikify draft ...`` — per-attempt draft + response IO + validation gate.

Subcommands::

    draft build <concept> [--task create|refine] [--corpus <c>] [--run <b>]
    draft show  <concept> [--run <b>] [--full] [--format text|json]
    draft check <concept> [--run <b>] [--format text|json]
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, Corpus, LayoutMismatchError
from ..bundle.draft.artifact import (
    draft_path,
    read_json,
    response_path,
    validation_path,
)
from ..bundle.draft.builder import build_draft, load_draft
from ..bundle.draft.validator import validate_response
from ._helpers import EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Per-attempt draft IO + validation gate.")


def _resolve_bundle(run_flag: Path | None) -> Bundle:
    if run_flag is not None:
        try:
            return Bundle.open(run_flag)
        except (LayoutMismatchError, FileNotFoundError) as exc:
            cli_error(EXIT_VALIDATION, error="bad_bundle", message=str(exc))
    cwd = Path.cwd()
    try:
        return Bundle.open(cwd)
    except (LayoutMismatchError, FileNotFoundError) as exc:
        cli_error(
            EXIT_VALIDATION,
            error="no_bundle_context",
            message=f"no v2 bundle resolved (cwd={cwd}); pass --run <bundle>. cause: {exc}",
        )


@app.command("build")
def cmd_build(
    concept: str = typer.Argument(...),
    task: str = typer.Option("create", "--task", help="create | refine"),
    corpus_dir: Path = typer.Option(..., "--corpus"),
    model_id: str = typer.Option(
        ...,
        "--model-id",
        help="Writer model identifier (e.g. claude-sonnet-4-6). Required.",
    ),
    tier: str = typer.Option(
        ...,
        "--tier",
        help="Writer cost tier — S | M | L. Required.",
    ),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Compile a WriteRequest for *concept* and write draft.json.

    ``--model-id`` and ``--tier`` are required; strategy lives in
    skills, not Python defaults.
    """
    bundle = _resolve_bundle(run)
    if task not in {"create", "refine"}:
        cli_error(EXIT_VALIDATION, error="bad_task", task=task)
    if tier not in {"S", "M", "L"}:
        cli_error(EXIT_VALIDATION, error="bad_tier", tier=tier)
    if not corpus_dir.is_dir():
        cli_error(
            EXIT_VALIDATION, error="not_a_directory", path=str(corpus_dir)
        )
    corpus = Corpus(root=corpus_dir)
    try:
        request = build_draft(
            bundle,
            slug=concept,
            corpus=corpus,
            task=task,
            model_id=model_id,
            tier=tier,
        )
    except FileNotFoundError as exc:
        cli_error(EXIT_VALIDATION, error="concept_not_found", message=str(exc))
    p = draft_path(bundle, concept)
    if fmt == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "draft_path": str(p),
                    "page_id": request.page_id,
                    "evidence_count": len(request.evidence_v2),
                }
            )
        )
        return
    typer.echo(f"draft:    {p}")
    typer.echo(f"page_id:  {request.page_id}")
    typer.echo(f"evidence: {len(request.evidence_v2)} chunks")


@app.command("show")
def cmd_show(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    full: bool = typer.Option(False, "--full"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Print the draft.json for a concept."""
    bundle = _resolve_bundle(run)
    p = draft_path(bundle, concept)
    if not p.is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", path=str(p))
    payload = read_json(p)
    if fmt == "json":
        if not full:
            # Trim heavy chunk_text fields to keep output token-light.
            for ev in payload.get("evidence_v2", []):
                if isinstance(ev, dict) and "chunk_text" in ev:
                    text = ev["chunk_text"]
                    ev["chunk_text"] = text[:500] + "..." if len(text) > 500 else text
        typer.echo(json.dumps(payload))
        return
    request = load_draft(bundle, concept)
    typer.echo(f"page_id:    {request.page_id}")
    typer.echo(f"page_kind:  {request.page_kind}")
    typer.echo(f"title:      {request.title}")
    typer.echo(f"aliases:    {request.aliases}")
    typer.echo(f"evidence:   {len(request.evidence_v2)} chunks")
    if full:
        for i, ev in enumerate(request.evidence_v2):
            preview = (ev.chunk_text or "")[:200]
            typer.echo(f"  e{i + 1}: {ev.chunk_id} ({ev.doc_id})")
            typer.echo(f"       quote: {ev.quote}")
            typer.echo(f"       chunk: {preview}")


@app.command("check")
def cmd_check(
    concept: str = typer.Argument(...),
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Validate response.json for *concept* against draft.json. Writes validation.json."""
    bundle = _resolve_bundle(run)
    if not draft_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="draft_not_found", concept=concept)
    if not response_path(bundle, concept).is_file():
        cli_error(EXIT_VALIDATION, error="response_not_found", concept=concept)
    verdict = validate_response(bundle, concept)
    if fmt == "json":
        typer.echo(json.dumps(verdict))
    else:
        ok = verdict["ok"]
        typer.echo(f"ok:        {ok}")
        typer.echo(f"page_id:   {verdict['page_id']}")
        typer.echo(f"verdict:   {validation_path(bundle, concept)}")
        if not ok:
            typer.echo(f"errors:    {len(verdict['errors'])}")
            for e in verdict["errors"][:10]:
                typer.echo(f"  [{e.get('code')}] {e.get('path')}: {e.get('message')}")
    if not verdict["ok"]:
        raise typer.Exit(code=EXIT_VALIDATION)


__all__ = ["app"]
