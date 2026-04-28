"""Top-level Typer CLI for wikify.

Seven noun-verb subapps. The skill-driven agent path is the canonical
interface; deterministic Python helpers (ingest pipeline, render, eval
metrics) are reachable through the appropriate noun (``corpus build``,
``render``, ``eval``).
"""

import sys

import typer

from . import corpus as corpus_cli
from . import draft as draft_cli
from . import eval as eval_cli
from . import render as render_cli
from . import run as run_cli
from . import wiki as wiki_cli
from . import work as work_cli


def _force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 so non-ASCII titles don't crash on Windows.

    The corpus contains titles with characters like ``‐`` (unicode
    hyphen) that are unrepresentable in cp1252, the default encoding for
    Windows console PYTHONIOENCODING. Without this, ``corpus find`` and
    ``corpus show`` raise ``UnicodeEncodeError`` mid-stream when piping
    or printing such titles.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Stream may already be wrapped (e.g., test runners) — skip.
            pass


_force_utf8_stdio()


app = typer.Typer(add_completion=False, help="wikify CLI")
app.add_typer(corpus_cli.app, name="corpus")
app.add_typer(run_cli.app, name="run")
app.add_typer(work_cli.app, name="work")
app.add_typer(draft_cli.app, name="draft")
app.add_typer(wiki_cli.app, name="wiki")
app.add_typer(render_cli.app, name="render")
app.add_typer(eval_cli.app, name="eval")


def main() -> None:
    from ._io import run_with_io_logging

    run_with_io_logging(app)


if __name__ == "__main__":
    main()
