"""HTTP server hosting the corpus CLI in a single warm process.

Phase 1 contract:

- One server, one corpus. Bind via ``--corpus`` (or ``WIKIFY_CORPUS``).
- Single endpoint ``POST /rpc`` accepts ``{"argv": [...]}`` and
  returns ``{"stdout": "...", "stderr": "...", "exit_code": N}``.
- Server invokes the existing Typer ``app`` via Click's
  ``CliRunner.invoke``, capturing stdout/stderr.
- Heavy state (vector store, knowledge graph, embedder) is kept warm
  by memoising the ``read_*`` helpers in ``wikify.corpus.chunks`` at
  startup. The first semantic call still pays the embedder cold-load;
  every subsequent call is warm.

Limitations (deferred to phase 2+):

- Foreground only — caller backgrounds the process if needed.
- Single-threaded handler. Concurrent requests serialise.
- No idle-shutdown, no graceful shutdown RPC, no discovery file.
- No auth — bind is loopback-only (``127.0.0.1``).
"""

from __future__ import annotations

import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Import lazily inside ``run_server`` so this module can be imported
# (e.g., for ``maybe_route_to_server``) without paying the
# typer/CliRunner cost.


def _free_port() -> int:
    """Pick an OS-assigned free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _install_warm_caches(corpus_root: Path) -> None:
    """Memoise the heavy ``read_*`` helpers for one corpus.

    This is the cheapest way to warm the server without refactoring
    every ``queries.*`` callsite to take an injected session. The
    cache is keyed by the absolute corpus root, so a cross-corpus
    request would still hit the cold loader (but the server is bound
    to one corpus anyway — see ``serve_design.md``).
    """
    import wikify.corpus.chunks as _chunks

    target = str(corpus_root.resolve())

    _doc_cache: dict[str, Any] = {}
    _chunk_cache: dict[str, Any] = {}
    _vec_cache: dict[str, Any] = {}
    _kg_cache: dict[str, Any] = {}

    _orig_list_documents = _chunks.list_documents
    _orig_all_chunks = _chunks.all_chunks
    _orig_read_vector_store = _chunks.read_vector_store
    _orig_read_kg = _chunks.read_knowledge_graph

    def _list_documents_cached(corpus):
        key = str(corpus.root.resolve())
        if key == target and key in _doc_cache:
            return _doc_cache[key]
        result = _orig_list_documents(corpus)
        if key == target:
            _doc_cache[key] = result
        return result

    def _all_chunks_cached(corpus):
        key = str(corpus.root.resolve())
        if key == target and key in _chunk_cache:
            return _chunk_cache[key]
        result = _orig_all_chunks(corpus)
        if key == target:
            _chunk_cache[key] = result
        return result

    def _read_vectors_cached(corpus):
        key = str(corpus.root.resolve())
        if key == target and key in _vec_cache:
            return _vec_cache[key]
        result = _orig_read_vector_store(corpus)
        if key == target:
            _vec_cache[key] = result
        return result

    def _read_kg_cached(corpus, vectors=None, embed_fn=None):
        # KG cache key must include whether an embed_fn is bound,
        # because the same KG object exposes a ``.search`` method that
        # closes over it. Cache one with-embed and one without.
        key = (str(corpus.root.resolve()), embed_fn is not None)
        if key[0] == target and key in _kg_cache:
            return _kg_cache[key]
        result = _orig_read_kg(corpus, vectors=vectors, embed_fn=embed_fn)
        if key[0] == target:
            _kg_cache[key] = result
        return result

    _chunks.list_documents = _list_documents_cached
    _chunks.all_chunks = _all_chunks_cached
    _chunks.read_vector_store = _read_vectors_cached
    _chunks.read_knowledge_graph = _read_kg_cached
    # ``queries.py`` imports these at module-load time, so we have to
    # patch the rebinding there too.
    import wikify.corpus.queries as _q

    _q.list_documents = _list_documents_cached
    _q.all_chunks = _all_chunks_cached
    _q.read_vector_store = _read_vectors_cached
    _q.read_knowledge_graph = _read_kg_cached


def _make_handler(app, corpus_root: Path):
    """Build a request handler closed over the warm Typer ``app``."""
    from typer.testing import CliRunner

    # Typer's CliRunner is a thin wrapper around click's that knows
    # how to translate a ``Typer`` instance into the underlying Click
    # command — using ``click.testing.CliRunner`` directly trips on
    # ``Typer`` having no ``.name`` attribute.
    runner = CliRunner()

    class RpcHandler(BaseHTTPRequestHandler):
        # Silence default per-request logging — we want our own
        # observability later, not stdlib's noisy output.
        def log_message(self, fmt: str, *args) -> None:
            return

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — stdlib API
            if self.path == "/health":
                self._send_json(
                    200,
                    {"ok": True, "corpus": str(corpus_root)},
                )
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802 — stdlib API
            if self.path != "/rpc":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            n = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": "bad_json", "message": str(exc)})
                return
            argv = body.get("argv")
            if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                self._send_json(
                    400,
                    {"ok": False, "error": "bad_argv", "message": "argv must be list[str]"},
                )
                return
            # Block re-entry: a request that asks the server to "serve"
            # or "repl" would deadlock the loop or fork a second daemon.
            if argv and argv[0] in {"serve", "repl"}:
                self._send_json(
                    400,
                    {
                        "ok": False,
                        "error": "not_routable",
                        "message": f"{argv[0]!r} is a server-mode command — run locally",
                    },
                )
                return
            # The client forwards a small allowlist of env vars
            # (WIKIFY_CLI_FORMAT etc.) inside the body so server-side
            # behaviour matches a local invocation. Apply them for the
            # duration of the call only.
            import os as _os

            client_env = body.get("env") or {}
            saved = {k: _os.environ.get(k) for k in client_env}
            for k, v in client_env.items():
                if isinstance(v, str):
                    _os.environ[k] = v
            try:
                result = runner.invoke(app, argv, catch_exceptions=False)
            finally:
                for k, prev in saved.items():
                    if prev is None:
                        _os.environ.pop(k, None)
                    else:
                        _os.environ[k] = prev
            self._send_json(
                200,
                {
                    "ok": True,
                    "stdout": result.stdout or "",
                    "stderr": (result.stderr or ""),
                    "exit_code": result.exit_code,
                },
            )

    return RpcHandler


def run_server(corpus_root: Path, *, port: int | None = None) -> None:
    """Start the HTTP server in the foreground.

    Prints ``WIKIFY_CORPUS_SERVER=http://127.0.0.1:<port>`` on the
    first stdout line so callers can capture it (or eval it). Then
    serves forever until SIGINT.
    """
    import wikify.cli as _cli_mod

    _install_warm_caches(corpus_root)
    use_port = port if port and port > 0 else _free_port()
    handler_cls = _make_handler(_cli_mod.app, corpus_root)
    server = HTTPServer(("127.0.0.1", use_port), handler_cls)
    url = f"http://127.0.0.1:{use_port}"
    # First line is machine-parseable; rest is human-friendly.
    # ``flush=True`` is critical: callers backgrounding the server
    # (e.g. ``$(wikify corpus serve | head -1 …)``) read the URL line
    # via a pipe, which Python buffers fully by default — without
    # the explicit flush the URL is held in the buffer until
    # ``serve_forever`` blocks, deadlocking the caller.
    print(f"WIKIFY_CORPUS_SERVER={url}", flush=True)
    print(f"corpus: {corpus_root}", file=sys.stderr, flush=True)
    print(f"pid:    {__import__('os').getpid()}", file=sys.stderr, flush=True)
    print(
        "ready (POST /rpc with {\"argv\": [...]}); Ctrl-C to stop",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        server.server_close()


__all__ = ["run_server"]
