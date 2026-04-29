"""Top-level wikify CLI entry point.

The fast path (``wikify corpus <verb> …`` against a running
``WIKIFY_CORPUS_SERVER``) avoids importing ``typer`` and the wikify
core entirely — it only touches ``os, sys, json, urllib`` from the
stdlib. That drops routed-call latency from ~1.2s to ~150-300ms on
cold cache.

The slow path (every other invocation) loads the full Typer app with
all 7 noun-verb subapps. The skill-driven agent path is the canonical
interface; deterministic Python helpers (ingest pipeline, render,
eval metrics) are reachable through the appropriate noun
(``corpus build``, ``render``, ``eval``).
"""

from __future__ import annotations

import os
import sys

# Forwarded to the server inside the RPC body so server-side behaviour
# matches a local invocation. Kept in sync with ``serve.client``.
_FORWARDED_ENV_VARS = (
    "WIKIFY_CLI_FORMAT",
    "WIKIFY_EMBED_VERBOSE",
    "WIKIFY_QUIET",
)
_LOCAL_ONLY_CORPUS_SUBCOMMANDS = frozenset({"serve", "repl", "build", "refresh"})


def _force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 + Unix line endings on Windows.

    Two reasons:

    - The corpus contains titles with characters like ``‐`` (unicode
      hyphen) that are unrepresentable in cp1252 (Windows default).
      Without UTF-8, ``corpus find`` / ``corpus show`` raise
      ``UnicodeEncodeError`` mid-stream when printing such titles.
    - Default Windows text-mode stdout translates ``\\n`` to ``\\r\\n``.
      That breaks the ``traverse … --format quiet | xargs traverse …``
      pattern documented in the search skill: ``xargs`` strips the
      ``\\n`` but not the ``\\r``, so each handle becomes
      ``doc:abc123\\r`` and resolves to ``handle_not_found``. Force
      ``newline=""`` so quiet output is byte-identical across platforms.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", newline="")
        except (OSError, ValueError, TypeError):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _maybe_route_to_server_fast() -> bool:
    """stdlib-only fast path: POST sys.argv to ``WIKIFY_CORPUS_SERVER`` and exit.

    Returns ``True`` after exiting (caller should stop). Returns
    ``False`` to fall through to the slow path (no server set, or the
    invocation isn't routable, or the server is unreachable).

    Critically: imports nothing from ``wikify.*`` and does not touch
    ``typer``. Only ``json`` + ``urllib`` from the stdlib (already
    loaded by the Python interpreter or imported lazily here).
    """
    url = os.environ.get("WIKIFY_CORPUS_SERVER", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return False
    argv = sys.argv
    if len(argv) < 3 or argv[1] != "corpus":
        return False
    if argv[2] in _LOCAL_ONLY_CORPUS_SUBCOMMANDS:
        return False

    import json
    import urllib.error
    import urllib.request

    env = {k: os.environ[k] for k in _FORWARDED_ENV_VARS if k in os.environ}
    payload = json.dumps({"argv": argv[1:], "env": env}).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rpc",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(
            f"warning: WIKIFY_CORPUS_SERVER={url} unreachable ({exc}); "
            f"falling back to in-process",
            file=sys.stderr,
        )
        return False

    if not body.get("ok"):
        print(json.dumps(body), file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(body.get("stdout") or "")
    sys.stdout.flush()
    err = body.get("stderr") or ""
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    sys.exit(int(body.get("exit_code", 0)))


def main() -> None:
    """CLI entry point: stdlib fast path first, full Typer app on miss."""
    _force_utf8_stdio()

    # Fast path: bypass typer + wikify core entirely.
    if _maybe_route_to_server_fast():
        return  # already exited

    # Slow path — full Typer app.
    from ._io import run_with_io_logging

    run_with_io_logging(_build_app())


def _build_app():
    """Construct the full Typer app. Heavy: imports all 7 subapps."""
    import typer

    from . import corpus as corpus_cli
    from . import draft as draft_cli
    from . import eval as eval_cli
    from . import render as render_cli
    from . import run as run_cli
    from . import wiki as wiki_cli
    from . import work as work_cli

    a = typer.Typer(add_completion=False, help="wikify CLI")
    a.add_typer(corpus_cli.app, name="corpus")
    a.add_typer(run_cli.app, name="run")
    a.add_typer(work_cli.app, name="work")
    a.add_typer(draft_cli.app, name="draft")
    a.add_typer(wiki_cli.app, name="wiki")
    a.add_typer(render_cli.app, name="render")
    a.add_typer(eval_cli.app, name="eval")
    return a


# Back-compat: tests and the in-process server import ``app`` directly
# from ``wikify.cli``. Build lazily via module ``__getattr__`` so the
# fast-path entry never pays the typer + subapp import cost.
def __getattr__(name: str):
    if name == "app":
        a = _build_app()
        globals()["app"] = a
        return a
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    main()
