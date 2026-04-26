"""``wikify render`` — static HTML site over a v2 bundle's committed wiki.

Single command::

    wikify render --run <bundle> --format html [--out <dir>] [--corpus <path>]

The render layer is deterministic and read-only; it consumes the same
``wiki/articles/`` + ``wiki/people/`` files that ``wikify wiki commit``
writes. The ``corpus`` argument lets the renderer stage figures from the
ingest tree; when omitted, the corpus path stored in ``run/state.json``
is used.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import Bundle, LayoutMismatchError
from ..bundle.run.state import load_state
from ..bundle.wiki.page import load_bundle as load_page_bundle
from ..render.html.render import build_site
from ._helpers import EXIT_VALIDATION, cli_error

app = typer.Typer(add_completion=False, help="Render a v2 bundle to a static site.")


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


@app.callback(invoke_without_command=True)
def cmd_render(
    ctx: typer.Context,
    run: Path | None = typer.Option(None, "--run"),
    fmt: str = typer.Option("html", "--format"),
    out: Path | None = typer.Option(None, "--out"),
    corpus: Path | None = typer.Option(None, "--corpus"),
    output_format: str = typer.Option("text", "--output-format"),
) -> None:
    """Render the committed wiki to a static site.

    ``--format`` selects the renderer; only ``html`` is implemented.
    ``--out`` defaults to ``<bundle>/derived/site``. ``--corpus`` lets
    callers override the corpus path used to stage figures (defaults to
    the value recorded in ``run/state.json``).
    """
    if ctx.invoked_subcommand is not None:
        return
    if fmt != "html":
        cli_error(
            EXIT_VALIDATION,
            error="unsupported_format",
            message=f"--format {fmt!r} not supported; use html",
        )

    bundle = _resolve_bundle(run)
    out_dir = out if out is not None else bundle.derived_dir / "site"

    if corpus is None:
        try:
            corpus = Path(load_state(bundle).corpus_path)
        except FileNotFoundError:
            corpus = None

    page_bundle = load_page_bundle(bundle.wiki_dir)
    site_dir = build_site(page_bundle, out_dir, corpus_root=corpus)

    if output_format == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "format": fmt,
                    "out": str(site_dir),
                    "pages": len(page_bundle.pages),
                }
            )
        )
        return
    typer.echo(f"rendered {len(page_bundle.pages)} page(s) -> {site_dir}")


__all__ = ["app"]
