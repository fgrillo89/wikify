"""``wikify mcp ...`` — agent-native access layer.

Verbs::

    wikify mcp serve [--corpus <path>] [--bundle <path>]
    wikify mcp status

``serve`` starts a stdio MCP server bound to one corpus and (optionally)
one bundle. Defaults to launch-time binding via ``WIKIFY_CORPUS`` /
``WIKIFY_BUNDLE`` env vars; ``--corpus`` / ``--bundle`` override.
Runtime rebinding is available via the ``context_set`` MCP tool.

``status`` prints the ``server_build`` snapshot that the currently
running server captured at startup, or an error if no PID file is found.
Compare the printed ``git_sha`` against ``git rev-parse --short HEAD``
to detect staleness.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="MCP server controls.")


def _pid_file_path() -> Path:
    """Return the PID file path (``$TMPDIR/wikify_mcp.pid``)."""
    return Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "wikify_mcp.pid"


@app.command("serve")
def cmd_serve(
    corpus_dir: Path | None = typer.Option(
        None, "--corpus",
        help="Bind a corpus at launch. Defaults to $WIKIFY_CORPUS.",
    ),
    bundle_dir: Path | None = typer.Option(
        None, "--bundle",
        help="Bind a bundle at launch. Defaults to $WIKIFY_BUNDLE.",
    ),
) -> None:
    """Start an MCP stdio server bound to a corpus (and optional bundle).

    Use this in ``.mcp.json`` to wire wikify into Claude Code::

        {
          "mcpServers": {
            "wikify": {
              "command": "uv",
              "args": ["run", "wikify", "mcp", "serve"],
              "env": {
                "WIKIFY_CORPUS": "data/corpora/<my-corpus>",
                "WIKIFY_BUNDLE": "data/wikis/<my-bundle>"
              }
            }
          }
        }
    """
    import atexit

    from ..mcp.context import bind_explicit, bind_from_env
    from ..mcp.server import _SERVER_BUILD, build_server

    bind_from_env()
    if corpus_dir is not None or bundle_dir is not None:
        bind_explicit(corpus_dir, bundle_dir)

    pid_path = _pid_file_path()
    pid_info = {"pid": os.getpid(), **_SERVER_BUILD}
    try:
        pid_path.write_text(json.dumps(pid_info), encoding="utf-8")
        atexit.register(lambda: pid_path.unlink(missing_ok=True))
    except OSError:
        pass  # PID file is best-effort; don't abort the server.

    build_server().run("stdio")


@app.command("status")
def cmd_status() -> None:
    """Print build info captured by the running MCP server at startup.

    Reads ``$TMPDIR/wikify_mcp.pid`` written when ``wikify mcp serve``
    started. Compare ``git_sha`` against ``git rev-parse --short HEAD``
    to detect whether the server is serving stale code.
    """
    pid_path = _pid_file_path()
    if not pid_path.exists():
        typer.echo(f"no running MCP server found at {pid_path}", err=True)
        raise typer.Exit(1)
    try:
        info = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"error reading {pid_path}: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(info, indent=2))
