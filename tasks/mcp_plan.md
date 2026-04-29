# MCP Server — implementation plan for all wikify surfaces

## Why MCP (not HTTP daemon)

The HTTP/curl daemon approach proven in the spike (closed PR #57)
solved server-side latency but left the bash round-trip in place: every
agent call still spawned a Python interpreter (~165ms floor). MCP
eliminates the bash layer entirely — Claude Code calls
`mcp__wikify__corpus_find(...)` as a native tool with sub-50ms
dispatch and structured args/responses.

Additional wins MCP gives that the HTTP daemon did not:

- **No env-var dance** — `.mcp.json` checked into the repo configures
  the server per-project; no `wikify corpus serve &` ritual at session
  start.
- **No process lifecycle problems** — Claude Code owns the server
  process; spawned at session open, killed at session close. No idle
  timeouts, no stale pid files, no per-corpus locking.
- **Structured tool args/responses** — typed via JSONSchema, no shell
  quoting, no manual parsing of CLI output.
- **One unified surface** — corpus + wiki + bundle work + ingest live
  in one server process sharing the warm corpus + wiki state.

## Scope: every wikify CLI surface, not just corpus

Today's CLI is 7 noun-verb subapps + ingest invoked under
`corpus build`. The MCP server exposes all of them as tools, grouped
by namespace. Inventory (verb counts):

| Namespace  | CLI verbs                                                          | MCP fit                  |
|------------|--------------------------------------------------------------------|--------------------------|
| `corpus`   | build, refresh, check, list[docs/chunks/files], find, show, traverse, schema, sample, repl | all tool-able except repl |
| `ingest`   | (currently invoked via `corpus build` / `corpus refresh`)          | first-class tools        |
| `wiki`     | list[articles/people/files], find, show, build, check, commit, schema, traverse, repl | all tool-able except repl |
| `work`     | list[claims/inbox/evidence], show, add[concept/evidence/feedback], set, claim, release, tend | all tool-able            |
| `draft`    | build, show, check                                                 | all tool-able            |
| `run`      | init, show, events, lock, unlock, close, set                       | all tool-able            |
| `render`   | (single render command)                                            | tool-able                |
| `eval`     | (single eval command)                                              | tool-able                |

Total expected tools: **~40-50**. Use MCP deferred-loading (only the
~5-8 most-called are eager; rest load on demand) to keep
always-loaded context overhead under 2K tokens.

## Architecture

### Bundle context = MCP server unit

A wikify bundle implies its corpus (the bundle records which corpus it
was built against). The natural unit for one MCP server is **one bundle
context = one corpus + one bundle's wiki/work state**. All tools share
warm corpus indexes + warm wiki graph + cached bundle state.

```json
{
  "mcpServers": {
    "wikify-ald-attempt-1": {
      "command": "wikify",
      "args": ["mcp", "serve"],
      "env": {
        "WIKIFY_CORPUS": "data/corpora/ald_all_marker",
        "WIKIFY_BUNDLE": "bundles/ald-2026-04-29"
      }
    }
  }
}
```

Multi-corpus / multi-bundle setups: configure multiple MCP servers in
`.mcp.json`; each is its own warm process. Claude sees them as
separate namespaces (`mcp__wikify-ald__corpus_find` vs
`mcp__wikify-ml__corpus_find`).

### Two binding modes

1. **Launch-time (default)** — `WIKIFY_CORPUS` (and optionally
   `WIKIFY_BUNDLE`) read at boot; server warms immediately; tools are
   ready when Claude initialises.
2. **Runtime (fallback)** — server starts unbound; `corpus_set(path)`
   and `bundle_set(path)` tools available; agent binds explicitly.
   Useful when the project has multiple corpora and the agent picks
   which to work on mid-session.

Hybrid: env wins if set; tool overrides at runtime.

### Transport: stdio (not HTTP)

MCP defaults to stdio (parent/child JSON-RPC). Claude pipes requests
to the server's stdin and reads responses from stdout. Latency
benefit over HTTP: no network stack, no port management, no firewall
surface. The Python MCP SDK (`mcp` package on PyPI) handles this; we
write tool decorators, the SDK handles the wire.

### Implementation: Python MCP SDK (not FastAPI)

`mcp` package is the official SDK. ~50 LOC of decorators replaces
~150 LOC of FastAPI handlers, and we don't need the
HTTP/uvicorn/MCP-adapter triple stack. FastAPI buys us OpenAPI +
validation + async — but the MCP SDK already gives us JSONSchema
generation from type hints + async support.

Rough server skeleton:

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from wikify.api import Bundle, Corpus
from wikify.corpus import queries

srv = Server("wikify")
_corpus: Corpus | None = None
_bundle: Bundle | None = None

@srv.tool()
async def corpus_find(
    query: str = "",
    top_k: int = 8,
    by: str = "chunk",
    rank: str = "semantic",
    text: bool = False,
) -> list[dict]:
    """Semantic / text / metric-ranked search over corpus chunks."""
    return queries.search_chunks(_require_corpus(), query, top_k=top_k)

# ... ~40 more @srv.tool() decorators wrapping queries.* / wiki.* / etc.

async def main() -> None:
    global _corpus, _bundle
    if "WIKIFY_CORPUS" in os.environ:
        _corpus = Corpus(root=Path(os.environ["WIKIFY_CORPUS"]))
        _warm_corpus(_corpus)
    if "WIKIFY_BUNDLE" in os.environ:
        _bundle = Bundle.open(Path(os.environ["WIKIFY_BUNDLE"]))
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())
```

The CLI entry point gains one verb: `wikify mcp serve`.

## Tool surface (per namespace)

### corpus_* (highest priority — most-called)

| Tool                | Wraps                                        | Eager / Deferred |
|---------------------|----------------------------------------------|------------------|
| `corpus_find`       | `queries.search_chunks` / `search_papers` / `search_authors` / `find_seeds` (now `sample`) | eager |
| `corpus_show`       | `queries.get_doc/chunk/figure/equation/author` | eager           |
| `corpus_traverse`   | `queries.traverse_doc/chunk/author`           | eager           |
| `corpus_check`      | `queries.check_corpus`                        | deferred         |
| `corpus_schema`     | static dict from `cli/corpus.py`              | deferred         |
| `corpus_list_docs`  | `queries.list_doc_ids`                        | deferred         |
| `corpus_list_chunks`| `queries.list_chunks_for_doc`                 | deferred         |
| `corpus_sample`     | `queries.sample_docs` (new — see below)       | deferred         |
| `corpus_set`        | rebind warm corpus to a new path              | deferred         |

### wiki_* (read-side mirrors corpus)

| Tool                | Wraps                                          |
|---------------------|------------------------------------------------|
| `wiki_find`         | bundle.wiki search                             |
| `wiki_show`         | bundle.wiki page show                          |
| `wiki_traverse`     | bundle.wiki page traverse (links/backlinks/evidence) |
| `wiki_list_articles`| bundle.wiki page enumeration                   |
| `wiki_list_people`  | bundle.wiki person-page enumeration            |
| `wiki_check`        | wiki coverage / thin pages                     |
| `wiki_schema`       | wiki traverse relations + node types           |
| `wiki_commit`       | commit a draft → wiki (mutation)               |
| `wiki_build`        | rebuild wiki index from committed pages        |

### work_* (bundle-state mutations)

| Tool                  | Wraps                                |
|-----------------------|--------------------------------------|
| `work_list_claims`    | open claims                          |
| `work_list_inbox`     | new feedback / bundle inbox          |
| `work_list_evidence`  | per-page evidence                    |
| `work_show`           | bundle status snapshot               |
| `work_add_concept`    | enqueue a new concept                |
| `work_add_evidence`   | attach evidence to a concept         |
| `work_add_feedback`   | append refinement feedback           |
| `work_set`            | mutate work state field              |
| `work_claim`          | claim a concept (lock)               |
| `work_release`        | release a claim                      |
| `work_tend`           | sweep stale claims                   |

### draft_* / run_* / render / eval

Direct mirrors of existing CLI verbs. Mostly mutation; latency matters
less, ergonomics matter (typed args, structured errors).

### ingest_* (NEW — promoted out of `corpus build/refresh`)

Today ingest is invoked via `corpus build` / `corpus refresh`. Promote
to first-class:

| Tool                 | Wraps                                                |
|----------------------|------------------------------------------------------|
| `ingest_corpus`      | `ingest.pipeline.ingest_corpus` (full pipeline)      |
| `ingest_refresh`     | `ingest.pipeline.refresh_corpus` (derived artifacts) |
| `ingest_check`       | manifest health, parser consistency                  |
| `ingest_step`        | run a single named step (parser / chunker / embed / graph) |
| `ingest_status`      | progress / last-success snapshot                     |

Phase 4 territory — ingest is a heavy long-running operation; will need
a streaming response mode (the SDK supports yielding tool progress).

## Lifecycle: bundle context

The MCP server's lifecycle aligns with the agent's bundle-work
session. Per the bundle-context model:

- **Spawn**: Claude Code reads `.mcp.json` at session open; spawns the
  server. Server reads env, binds corpus + bundle, warms KG/embedder
  in a background task so first tool call doesn't block.
- **Run**: tools execute, share warm state. Bundle mutations are
  serialised through the existing lock layer (`bundle/run/state.py`,
  `bundle/run/events.py`).
- **Shutdown**: Claude Code SIGTERMs at session close. Server flushes
  any in-flight events, exits.

Per-bundle `.mcp.json` example for an agent working on ALD:

```json
{
  "mcpServers": {
    "wikify": {
      "command": "wikify",
      "args": ["mcp", "serve"],
      "env": {
        "WIKIFY_CORPUS": "data/corpora/ald_all_marker",
        "WIKIFY_BUNDLE": "bundles/ald-attempt-1"
      }
    }
  }
}
```

Multi-bundle (e.g. comparing two builds):

```json
{
  "mcpServers": {
    "wikify-baseline": { "command": "wikify", "args": ["mcp", "serve"], "env": {"WIKIFY_BUNDLE": "bundles/baseline"}},
    "wikify-guided":   { "command": "wikify", "args": ["mcp", "serve"], "env": {"WIKIFY_BUNDLE": "bundles/guided"}}
  }
}
```

## Testing strategy

Three layers, no duplication:

1. **Inner-API tests (existing)** — `test_corpus_queries.py`,
   `test_bundle_*.py`. Data-correctness lives here. Both CLI and MCP
   wrap these; one test set covers both surfaces.
2. **CLI tests (existing, retained)** — `test_cli_corpus.py` etc.
   Cover format selection, env routing, structured envelopes, exit
   codes. CLI-specific concerns; humans + bash scripts still consume.
3. **MCP tests (new)** — `test_mcp_corpus.py`,
   `test_mcp_wiki.py`, etc. Use the `mcp` SDK's in-process test
   harness. Verify each tool wraps the inner API correctly + returns
   the documented schema. ~10-15 tests per namespace, ~150-300 lines
   per file.

Forcing-function side benefit: building the MCP surface pushes any
inline CLI logic (re-ranking, formatting, envelope construction) down
into `queries.*` / `bundle.*`, where both surfaces can call it. Net
code goes down, not up.

## Phasing

- **Phase 0 (prereq, this branch's follow-up)** — CLI completion.
  `find --seed` → `corpus sample [--strategy diverse|...]`. Removes
  the seed-specific wording so the MCP tool surface gets the right
  noun (`corpus_sample`, not `corpus_seed`). Gets the CLI to a stable
  shape before MCP locks the tool names.
- **Phase 1** — corpus MCP. ~10 tools wrapping `queries.*`. Ships
  the SDK integration end-to-end + the in-process test harness.
  Smallest viable; agent still uses bash for non-corpus calls.
- **Phase 2** — wiki MCP. Same pattern; smaller payload.
- **Phase 3** — work / draft / run MCP. Mutation surface; mostly
  ergonomic + structured-error wins.
- **Phase 4** — ingest MCP. Streaming progress for long-running
  builds.
- **Phase 5** — render / eval MCP. Last because they're called
  least.

Per phase: skill files updated to teach the MCP surface alongside
(not replacing) the CLI. The skill auto-detects MCP availability and
prefers it; falls back to bash CLI when no MCP server is configured.

## Skill updates needed

Each `wikify-*` skill file gets an MCP-mode section:

- `wikify-search-corpus` — Phase 1
- `wikify-search-wiki` — Phase 2
- `wikify-bundle` — Phase 3
- `wikify-ingest` (new) — Phase 4

The shared `wikify` skill grows a top-level "MCP setup" reference
documenting the `.mcp.json` shape and the `corpus_set` / `bundle_set`
tools.

## Out of scope (deferred to future plans)

- HTTP/SSE transport (in addition to stdio) — only matters if non-
  Claude consumers (web UI, scripts) want the server.
- Multi-tenant single server holding many corpora — not worth the
  cache eviction complexity; per-corpus servers are cheap.
- Background warming of multiple bundles per server — same reason.
- Persistent KG cache across server restarts (e.g. pickle file) —
  Phase 6 if startup cost becomes a real complaint.

## Decision log

- **Chose MCP SDK over FastAPI**: SDK handles stdio + JSONSchema; less
  code, fewer deps.
- **Chose bundle-context (not corpus-only) as server unit**: covers
  all 7 namespaces in one warm process, matches how agents actually
  work (one bundle at a time).
- **Chose launch-time binding (not auto-spawn)**: Claude Code owns the
  lifecycle via `.mcp.json`; no pid files, no idle timeouts, no
  cross-session locking.
- **Chose deferred tool loading**: keeps always-loaded context
  overhead under 2K tokens; the 5-8 most-called tools are eager.
- **Chose to retain the CLI**: humans, bash scripts, and the existing
  test suite all consume it. MCP is additive.
