"""Server-scoped corpus/bundle binding for the MCP adapter.

The MCP server is a single warm process bound to one corpus and at
most one bundle (per ``tasks/mcp_plan.md``). Multi-corpus comparisons
are out of scope; if needed, configure multiple ``mcpServers`` entries.

Binding modes:

- launch-time (preferred): ``WIKIFY_CORPUS`` / ``WIKIFY_BUNDLE`` env
  vars are read once when the server starts (:func:`bind_from_env`).
- runtime: the ``context_set`` MCP tool calls :func:`bind` to rebind.

Stdio transport runs single-threaded async; no locking needed.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..api import Bundle, Corpus

_corpus: Corpus | None = None
_bundle: Bundle | None = None


class ContextError(Exception):
    """Raised when a required context binding is missing or invalid."""


def reset() -> None:
    """Drop any active binding. Used by tests; harmless in production."""
    global _corpus, _bundle
    _corpus = None
    _bundle = None


def bind(*, corpus_path: str | Path | None = None,
         bundle_path: str | Path | None = None,
         clear_bundle: bool = False) -> None:
    """Bind the active corpus and/or bundle.

    Passing ``None`` for ``corpus_path`` keeps the current corpus
    binding. To clear the bundle binding without re-binding the corpus,
    pass ``clear_bundle=True``.
    """
    global _corpus, _bundle
    if corpus_path is not None:
        path = Path(corpus_path)
        if not path.is_dir():
            raise ContextError(f"corpus path is not a directory: {path}")
        _corpus = Corpus(root=path)
    if bundle_path is not None:
        path = Path(bundle_path)
        _bundle = Bundle.open(path)
    elif clear_bundle:
        _bundle = None


def bind_explicit(corpus_path: str | Path | None,
                  bundle_path: str | Path | None) -> None:
    """Bind from explicit CLI flags. ``None`` means leave that slot empty."""
    if corpus_path is not None:
        bind(corpus_path=corpus_path)
    if bundle_path is not None:
        bind(bundle_path=bundle_path)


def bind_from_env() -> None:
    """Bind from ``WIKIFY_CORPUS`` / ``WIKIFY_BUNDLE`` if set.

    Missing env vars leave the binding empty; the first tool call that
    needs a corpus will surface ``no_corpus_bound``.
    """
    corpus_env = os.environ.get("WIKIFY_CORPUS")
    bundle_env = os.environ.get("WIKIFY_BUNDLE")
    if corpus_env:
        bind(corpus_path=corpus_env)
    if bundle_env:
        bind(bundle_path=bundle_env)


def get_corpus() -> Corpus | None:
    return _corpus


def get_bundle() -> Bundle | None:
    return _bundle


def require_corpus() -> Corpus:
    """Return the bound corpus or raise :class:`ContextError`."""
    if _corpus is None:
        raise ContextError(
            "no corpus bound; set WIKIFY_CORPUS at launch or call "
            "context_set(corpus_path=...)"
        )
    return _corpus


def snapshot() -> dict:
    """Return a serialisable summary of the current binding."""
    return {
        "corpus_path": str(_corpus.root) if _corpus is not None else None,
        "bundle_path": str(_bundle.root) if _bundle is not None else None,
        "corpus_bound": _corpus is not None,
        "bundle_bound": _bundle is not None,
    }
