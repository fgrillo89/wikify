"""Thin client that forwards CLI invocations to a running corpus server.

Hooked into ``wikify.cli.main`` via :func:`maybe_route_to_server`. When
``WIKIFY_CORPUS_SERVER`` is set to an ``http://host:port`` URL, the
client POSTs ``sys.argv`` and prints the response, exiting with the
server's reported exit code. When unset (the common case), returns
``False`` so the local Typer app runs as usual.

Phase 1 routing scope:

- All ``wikify corpus …`` commands route — that is the audit-relevant
  surface and the only namespace served.
- ``wikify corpus serve`` and ``wikify corpus repl`` are local-only by
  definition; routed requests for them would deadlock the loop or
  fork a second server. The client detects them and falls through.
- Other top-level subapps (``wiki``, ``run``, ``draft`` …) are also
  local-only for now — the server only hosts the corpus surface.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_ENV = "WIKIFY_CORPUS_SERVER"
_TIMEOUT_S = 30.0  # generous — first semantic call still cold-loads the embedder.

_LOCAL_ONLY_CORPUS_SUBCOMMANDS = frozenset({"serve", "repl", "build", "refresh"})

# Client env that must reach the server unchanged: format defaults,
# verbosity toggles, hint suppression. Forwarded inside the RPC body
# so the server can re-set them in os.environ for the duration of the
# call. Server-side state like WIKIFY_CORPUS / WIKIFY_CORPUS_SERVER
# itself is intentionally excluded.
_FORWARDED_ENV_VARS = (
    "WIKIFY_CLI_FORMAT",
    "WIKIFY_EMBED_VERBOSE",
    "WIKIFY_QUIET",
)


def _resolve_server_url() -> str | None:
    raw = os.environ.get(_ENV, "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        return None
    return raw.rstrip("/")


def _is_routable(argv: list[str]) -> bool:
    """Only ``wikify corpus <verb> …`` (with verb not in the local-only set) routes.

    The exact shape we route for is::

        sys.argv == ["wikify", "corpus", <verb>, ...]

    Anything else falls through to local execution.
    """
    if len(argv) < 3:
        return False
    if argv[1] != "corpus":
        return False
    return argv[2] not in _LOCAL_ONLY_CORPUS_SUBCOMMANDS


def maybe_route_to_server() -> bool:
    """Forward ``sys.argv`` to ``WIKIFY_CORPUS_SERVER`` if applicable.

    Returns ``True`` when the request was routed (caller should exit
    immediately — :func:`sys.exit` has already been called) and
    ``False`` when execution should continue locally.
    """
    url = _resolve_server_url()
    if url is None:
        return False
    if not _is_routable(sys.argv):
        return False
    # Strip the program name; the server reconstructs the typer app
    # invocation from the remaining tokens. Forward the format /
    # verbosity env vars so behaviour matches a local invocation.
    env = {k: os.environ[k] for k in _FORWARDED_ENV_VARS if k in os.environ}
    payload = json.dumps({"argv": sys.argv[1:], "env": env}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/rpc",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        # Server unreachable — degrade gracefully to local execution
        # rather than failing the whole call. Print a one-line warning
        # so the user knows the daemon isn't being used.
        print(
            f"warning: {_ENV}={url} unreachable ({exc}); falling back to in-process",
            file=sys.stderr,
        )
        return False

    if not body.get("ok"):
        # Server returned a structured error before invoking — print
        # the envelope on stderr and exit with validation code (1).
        print(json.dumps(body), file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(body.get("stdout") or "")
    sys.stdout.flush()
    err = body.get("stderr") or ""
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    sys.exit(int(body.get("exit_code", 0)))


__all__ = ["maybe_route_to_server"]
