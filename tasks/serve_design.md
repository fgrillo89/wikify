# `wikify corpus serve` — design

## Why

Cold-start floor for any one-shot CLI call is **~1.2s** (Python +
Typer + wikify core import). Cold semantic call is **~4.8s** (+ first
embedder load). Every agent invocation pays both. The same operations
inside `wikify corpus repl` warm-call in **~80ms** because the process
keeps `Corpus` + KG + embedder alive.

Goal: collapse the per-call floor for agent-driven exploration without
forcing agents to plan their queries upfront (the "script" model).

## Lifecycle

**Agent-started, file-discovered, idle-shutdown.** Decisions:

- The agent (or the user) starts the server explicitly: `wikify serve
  --background`. Server resolves the corpus via the standard order
  (`--corpus` > `WIKIFY_CORPUS` > cwd walk-up), binds, daemonises,
  exits 0 on the parent.
- The CLI client (`wikify corpus find …`) auto-detects a running
  server for the resolved corpus by reading a discovery file at
  `~/.cache/wikify/daemon-<corpus_hash>.json` (or
  `%LOCALAPPDATA%\wikify\daemon-<corpus_hash>.json` on Windows). If
  alive, route through it; if not, fall back to in-process execution
  (no behaviour change).
- `WIKIFY_DAEMON=0` disables auto-routing — escape hatch for
  debugging.
- Server exits after **N=600s** (10 min) of no requests. Configurable
  via `--idle-timeout`.
- `wikify shutdown` sends a graceful-exit RPC.

`<corpus_hash>` is the SHA-1 of the absolute, normalised corpus root
path. One server per corpus; concurrent corpora get their own.

## Transport

**Localhost HTTP**. Loopback-only (`127.0.0.1:<port>`), random port at
start, written to the discovery file. Reasons over Unix
sockets/Windows named pipes:

- Cross-platform out of the box.
- Trivially debuggable with `curl`.
- Lets a future MCP server façade reuse the same surface.
- Localhost overhead < 5ms — irrelevant compared to 1.2s save.

Single endpoint: `POST /rpc`, request body:

```json
{
  "op": "find" | "show" | "traverse" | "check" | "list_docs" | "list_chunks" | "schema",
  "args": { ... },
  "fmt": "compact" | "quiet" | "json"
}
```

Response body:

```json
{ "ok": true,  "stdout": "...", "stderr": "" }
{ "ok": false, "error": "bad_handle", "message": "...", ... }
```

## Discovery file

`~/.cache/wikify/daemon-<corpus_hash>.json`:

```json
{
  "pid": 12345,
  "url": "http://127.0.0.1:54321",
  "corpus": "/abs/path/to/corpus",
  "started_at": "2026-04-29T17:30:00Z",
  "version": "wikify 0.1.0"
}
```

Liveness check before routing: `os.kill(pid, 0)` on POSIX; `OpenProcess`
on Windows. Stale file → delete + fall back to in-process. This
catches the `kill -9` and OOM-kill cases.

## Server internals

Holds **one** `CorpusSearchSession` (already exists) — keeps doc/chunk
indexes warm immediately, embedder + KG lazy on first semantic call
(matches the REPL behaviour today).

Single-threaded request handling for v1. Most agent flows are serial;
concurrency adds locking complexity around the embedder. v2 can
add an asyncio pool if measurement shows contention.

The RPC handlers wrap the same `wikify.corpus.queries.*` API the CLI
already uses. No duplication of business logic.

## Why HTTP and not raw socket?

Single answer: optionality. The same server can later expose:

- An MCP façade for Claude Code (kills the bash round-trip entirely)
- A simple web debug UI (`/debug` showing recent queries, KG stats)
- Multi-process clients without re-plumbing the protocol

Raw sockets save ~120 LOC and ~3ms per call. Not worth losing the
above. Localhost HTTP overhead measured at < 2ms in Python's stdlib.

## Phasing

- **Phase 1 (this PR)**: `wikify serve --corpus X --foreground` (no
  daemonising), `WIKIFY_CORPUS_SERVER=http://...` env override,
  client routing for `find`/`show`/`traverse`/`schema`/`check`.
  Foreground means the agent runs `wikify serve --foreground &` in
  bash, captures the URL from a startup line, exports the env. No
  discovery file, no idle shutdown, no SIGTERM handler. Proves the
  latency win.
- **Phase 2**: `--background` (daemonise), discovery file, liveness
  check, fall-back routing, `wikify shutdown` command. This is the
  "agent doesn't have to think about it" version.
- **Phase 3** (deferred): idle shutdown, concurrent requests if
  needed, MCP façade.

## Targets

| call                   | one-shot now | warm via server |
|------------------------|--------------|-----------------|
| `find "X"` semantic    | 4.8s         | ~80ms (60x)     |
| `find --by paper …`    | 1.9s         | ~30ms           |
| `traverse … --to X`    | 1.9s         | ~20ms           |
| `show doc:X`           | 1.3s         | ~10ms           |

Numbers from the audit profiling (one-shot) and REPL warm-call
estimate (~30-80ms based on the embedder health-check timing).
