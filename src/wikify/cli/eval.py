"""``wikify eval`` — corpus-free metrics over a v2 bundle's committed wiki.

Single command::

    wikify eval --run <bundle> [--report <path>]

Runs the metric suite that does not need a corpus handle or an embedder
(graph-shape and figure-reference metrics, plus page counts). Heavier
metrics that require ``chunk_embeddings`` / ``chunk_text`` (M1, M6, GT-C)
are intentionally not wired here yet — those flow through a follow-up
verb that takes ``--corpus`` once the corpus-side embedding adapter has
landed.

The output schema is::

    {
      "schema_version": 1,
      "n_articles": int,
      "n_people": int,
      "g_evidence": {modularity, spectral_gap, n_nodes, n_edges},
      "g_links": {modularity, spectral_gap, n_nodes, n_edges},
      "figures": {n_figures_referenced_in_bodies, n_total_captions, figure_reference_rate},
    }
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, LayoutMismatchError
from ..bundle.wiki.page import load_bundle as load_page_bundle
from ..eval.metrics import (
    figure_reference_counts,
    g_links_modularity,
    spectral_gap_modularity,
)
from ._helpers import EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Eval metrics over a v2 bundle.")

_REPORT_SCHEMA_VERSION = 1


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


def _to_jsonable(value):
    """Convert NaN/inf to None so the JSON report stays valid JSON."""
    import math

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


@app.callback(invoke_without_command=True)
def cmd_eval(
    ctx: typer.Context,
    run: Path | None = typer.Option(None, "--run"),
    report: Path | None = typer.Option(None, "--report"),
    fmt: str = typer.Option("text", "--format"),
) -> None:
    """Compute corpus-free metrics over the bundle's committed wiki."""
    if ctx.invoked_subcommand is not None:
        return
    bundle = _resolve_bundle(run)
    page_bundle = load_page_bundle(bundle.wiki_dir)

    payload = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "n_articles": len(page_bundle.concepts),
        "n_people": len(page_bundle.people),
        "g_evidence": spectral_gap_modularity(page_bundle),
        "g_links": g_links_modularity(page_bundle),
        "figures": figure_reference_counts(page_bundle),
    }
    payload = _to_jsonable(payload)

    report_path = report if report is not None else bundle.derived_dir / "eval.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if fmt == "json":
        typer.echo(json.dumps({"ok": True, "report": str(report_path), **payload}))
        return
    typer.echo(f"articles:       {payload['n_articles']}")
    typer.echo(f"people:         {payload['n_people']}")
    typer.echo(f"G_evidence Q:   {payload['g_evidence']['modularity']:.4f}")
    typer.echo(f"G_links Q:      {payload['g_links']['modularity']:.4f}")
    typer.echo(f"report:         {report_path}")


__all__ = ["app"]
