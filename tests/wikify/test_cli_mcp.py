"""CLI tests for ``wikify mcp ...``.

The MCP layer's behavior is covered by ``test_mcp_corpus.py``. This
file only confirms the CLI verb is registered and surfaces help text;
running the actual stdio loop is out of scope for unit tests.
"""

from __future__ import annotations

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def test_mcp_subapp_registered() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_mcp_serve_help_lists_bind_flags() -> None:
    result = runner.invoke(app, ["mcp", "serve", "--help"])
    assert result.exit_code == 0
    assert "--corpus" in result.output
    assert "--bundle" in result.output
