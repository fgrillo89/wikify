"""``wikify mcp ...`` — agent-native access layer.

Currently exposes one verb::

    wikify mcp serve [--corpus <path>] [--bundle <path>]

Starts a stdio MCP server bound to one corpus and (optionally) one
bundle. Defaults to launch-time binding via ``WIKIFY_CORPUS`` /
``WIKIFY_BUNDLE`` env vars; ``--corpus`` / ``--bundle`` override.
Runtime rebinding is available via the ``context_set`` MCP tool.
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="MCP server controls.")


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
              "command": "wikify",
              "args": ["mcp", "serve"],
              "env": {
                "WIKIFY_CORPUS": "data/corpora/<my-corpus>",
                "WIKIFY_BUNDLE": "data/wikis/<my-bundle>"
              }
            }
          }
        }
    """
    from ..mcp.context import bind_explicit, bind_from_env
    from ..mcp.server import build_server

    bind_from_env()
    if corpus_dir is not None or bundle_dir is not None:
        bind_explicit(corpus_dir, bundle_dir)
    build_server().run("stdio")
