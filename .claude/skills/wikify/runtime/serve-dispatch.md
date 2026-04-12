---
name: wikify/runtime/serve-dispatch
description: Long-running loop that services file-dispatch requests from a wikify distill or query harness.
---

# serve-dispatch

## When to use this skill
Invoke this skill when a `wikify` CLI process is running and needs a Claude session to handle its dispatch requests.

## How it works
The Python harness (`wikify distill ...` or `wikify query ...`) writes request JSON files to role subdirectories under the dispatch dir (default `data/dispatch/`, overridable via `WIKIFY_DISPATCH_DIR`).

The dispatch roles are:
- `extract/` — chunk extraction (handler: `handlers/extract`)
- `write/` — page writing (handler: `handlers/write`)
- `compact/` — dossier compaction (handler: `handlers/compact`)
- `edit/` — editor brief (handler: `handlers/edit`)
- `orchestrate/` — LLM-policy action selection (handler: `handlers/orchestrate`)
- `query/` — query answering (handler: `handlers/query`)

## Session-scoped prompt-layer cache

Before processing any requests, initialize an empty `{hash: text}` dict in memory. Pass this cache object into every write handler invocation for the lifetime of this serve-dispatch session.

The write handler reads each write request's hash fields (`style_guide_hash`, `field_guide_hash`, `artifact_template_hash`, `corpus_persona_hash`), fetches uncached layers from `<bundle_root>/_meta/prompt_layers/<hash>.md`, stores them in the shared dict, and composes the system prompt locally. This means each unique stable layer is read from disk and sent to the model at most once per serve-dispatch session regardless of how many write requests arrive.

**VENDOR NEUTRAL**: no Anthropic or OpenAI SDK prompt-caching primitive is used. The cache is a plain Python dict in session memory.

## Steps
1. Resolve the dispatch dir (env var `WIKIFY_DISPATCH_DIR` or default `data/dispatch`).
2. Initialize the session-scoped prompt-layer cache (see above).
3. Poll every 50ms for files matching `*/<rid>.request.json`. On each tick, scan the entire dispatch dir and collect ALL pending request files before processing any. Where the hosting Claude Code session supports parallel tool invocations, dispatch all pending handlers concurrently rather than sequentially — this is what allows `extract_many` to achieve batch-parallel speedup.
4. For each request file:
   a. Identify the role from the parent directory name.
   b. Invoke the corresponding handler skill (`wikify/handlers/<role>`), passing the prompt-layer cache for write-role requests.
   c. The handler writes `<rid>.response.json` next to the request.
5. Continue until ANY of the following exit conditions:
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
This skill services file-dispatch requests from the Python harness. The harness writes request files and blocks until this skill writes the corresponding response files.
