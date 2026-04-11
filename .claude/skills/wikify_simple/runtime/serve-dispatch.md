---
name: wikify_simple/runtime/serve-dispatch
description: Long-running loop that services file-dispatch requests from a wikify_simple distill or query harness.
---

# serve-dispatch

## When to use this skill
Invoke this skill when a `wikify-simple` CLI process is running with `--binding file_dispatch` and needs a Claude session to handle its dispatch requests. This is the ONLY way to service `file_dispatch` runs; the `fake` and `heuristic` bindings do not dispatch to skills at all.

## How it works
The Python harness (`wikify-simple distill ...` or `wikify-simple query ...`) writes request JSON files to role subdirectories under the dispatch dir (default `data/dispatch/`, overridable via `WIKIFY_SIMPLE_DISPATCH_DIR`).

The dispatch roles are:
- `extract/` — chunk extraction (handler: `handlers/extract`)
- `write/` — page writing (handler: `handlers/write`)
- `compact/` — dossier compaction (handler: `handlers/compact`)
- `edit/` — editor brief (handler: `handlers/edit`)
- `orchestrate/` — LLM-policy action selection (handler: `handlers/orchestrate`)
- `query/` — query answering (handler: `handlers/query`)

## Steps
1. Resolve the dispatch dir (env var `WIKIFY_SIMPLE_DISPATCH_DIR` or default `data/dispatch`).
2. Poll every 50ms for files matching `*/<rid>.request.json`. On each tick, scan the entire dispatch dir and collect ALL pending request files before processing any. Where the hosting Claude Code session supports parallel tool invocations, dispatch all pending handlers concurrently rather than sequentially — this is what allows `extract_many` to achieve batch-parallel speedup.
3. For each request file:
   a. Identify the role from the parent directory name.
   b. Invoke the corresponding handler skill (`wikify_simple/handlers/<role>`).
   c. The handler writes `<rid>.response.json` next to the request.
4. Continue until ANY of the following exit conditions:
   - The orchestrator returns a `done` action (watch for `orchestrate/<rid>.response.json` payloads with `{"name": "done"}`).
   - The harness process exits (you see no new request files for 30 consecutive polls AND all existing request files already have matching `.response.json` or `.error.json`).
   - You receive an explicit stop signal (the user interrupts).

## What the skill does NOT do
- Does not read bundle files or the corpus directly.
- Does not make decisions about extract/write content (that's the handlers' job).
- Does not track budget (the Python cost meter handles that).
- Does not retry failed requests beyond the handler-local retry (one retry).

## Errors
If a handler writes `<rid>.error.json`, the Python harness will log it, skip the request, and continue. You should do the same: log it to stdout and move to the next request. DO NOT block the loop on errors.

## Important
Only needed when `--binding file_dispatch`. The `fake` and `heuristic` bindings execute in-process with no file dispatch.
