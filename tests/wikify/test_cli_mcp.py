"""CLI tests for ``wikify mcp ...``.

The MCP layer's behavior is covered by ``test_mcp_corpus.py``. This
file only confirms the CLI verb is registered and surfaces help text;
running the actual stdio loop is out of scope for unit tests.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def test_mcp_subapp_registered() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_mcp_serve_help_lists_bind_flags() -> None:
    # Introspect the registered command instead of scraping rendered help:
    # rich reflows/truncates option columns at narrow terminal widths, so a
    # substring check on the boxed help is brittle across widths and versions.
    from typer.main import get_command

    serve = get_command(app).commands["mcp"].commands["serve"]
    opts = {opt for param in serve.params for opt in param.opts}
    assert "--corpus" in opts
    assert "--bundle" in opts


def test_mcp_status_registered() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output


def test_mcp_status_no_pid_file(tmp_path, monkeypatch) -> None:
    """mcp status exits non-zero when PID file is absent."""
    import wikify.cli.mcp as mcp_mod

    absent = tmp_path / "wikify_mcp.pid"
    monkeypatch.setattr(mcp_mod, "_pid_file_path", lambda: absent)
    result = runner.invoke(app, ["mcp", "status"])
    assert result.exit_code != 0


def test_mcp_status_prints_build_info(tmp_path, monkeypatch) -> None:
    """mcp status prints JSON with package_version and git_sha when PID file exists."""
    import wikify.cli.mcp as mcp_mod

    pid_file = tmp_path / "wikify_mcp.pid"
    build_info = {
        "pid": 12345,
        "package_version": "0.1.0",
        "git_sha": "abc1234",
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    pid_file.write_text(json.dumps(build_info), encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_pid_file_path", lambda: pid_file)

    result = runner.invoke(app, ["mcp", "status"])
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["package_version"] == "0.1.0"
    assert out["git_sha"] == "abc1234"
