"""MCP server adapter for Wikify.

A sibling adapter to :mod:`wikify.cli`. Both call into the same
domain APIs in :mod:`wikify.corpus.queries` (and friends in later
phases). Phase 1 ships the corpus tool surface and corpus resources;
wiki, bundle, mutations, and ingest/render/eval come later.

The CLI verb ``wikify mcp serve`` is the user-facing entry point;
:func:`build_server` is the testable factory.
"""

from .context import bind, bind_explicit, bind_from_env, snapshot
from .server import build_server

__all__ = [
    "bind",
    "bind_explicit",
    "bind_from_env",
    "build_server",
    "snapshot",
]
