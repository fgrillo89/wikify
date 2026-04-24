---
name: wikify/reference/cli-tool-surface
description: The wikify CLI command grammar, conventions, and invocation rules for agent-driven workflows.
---

# CLI tool surface

The `wikify` CLI is the deterministic edge between the agent and the Python
backend. It is designed like the Unix tool surface LLMs already handle well:
narrow, composable, typed, and explicit about side effects.

## Command families

Eight stable families. Do not invent new families.

- `wikify session ...` — create, inspect, mutate, checkpoint, close the run session.
- `wikify kg ...` — query the corpus knowledge graph (seeds, abstracts, evidence, similar chunks).
- `wikify draft ...` — build request artifacts the subagent will consume.
- `wikify validate ...` — structural and schema validation of scratch artifacts.
- `wikify bundle ...` — promote validated artifacts into canonical bundle files; rebuild indices.
- `wikify render ...` — static-site rendering (currently aliased to `wikify html`).
- `wikify eval ...` — metric computation over a rendered bundle.
- `wikify ingest ...` — corpus parse/chunk/embed/graph.

## Conventions

### Stdout and exit codes

- Default stdout is token-light JSON: IDs, summaries, counts. The agent reads it with `Read` or pipes through `jq`.
- `--full` on read commands opts in to heavy payloads (page bodies, full chunk text, etc.).
- Exit code `0` = success. Non-zero = a documented failure mode; structured JSON on stderr: `{"error": "<code>", "message": "<human>", "details": {...}}`.
- A command that would emit more than ~64 KB to stdout writes to a scratch file and returns the path instead. The agent reads the path only if it needs the content in context.

### Session binding

- Every command that mutates durable state takes `--session <path>` explicitly.
- No command infers the session from the working directory or an environment variable.
- `wikify session init` is the only command that creates a session file; everything else requires an existing session.

### Locking

- Mutating commands attempt `session lock` implicitly and release on completion.
- If another actor holds the lock, the command fails fast with exit code `2` (`lock_held`) and a structured error.
- `wikify session lock --force` exists for stuck locks; use with care — documented in `session.lock` metadata.

### Schema versioning

- Every command that reads a file asserts `schema_version` matches the expected value for its code path.
- A mismatch exits non-zero with `schema_version_mismatch`. Skills do not paper over this.

### No hidden state

- Every command is a pure function of flags, file paths, corpus files, and explicit session files.
- No environment-variable-driven behavior (except `WIKIFY_TEST_FAKES` gated test-only injection).
- No implicit "last run" state.

## Representative invocations

### Session

```
wikify session init --bundle <path> --corpus <path> --strategy baseline [--budget-target 5000]
wikify session show --session <path>                   # token-light JSON
wikify session show --session <path> --full            # full JSON
wikify session update --session <path> --patch '{"pages":[{"page_id":"X","status":"drafted"}]}'
wikify session checkpoint --session <path> --label "after-seed-selection"
wikify session close --session <path>                  # flushes final _run.json
wikify session lock --session <path> [--owner <name>] [--ttl-seconds 3600]
wikify session unlock --session <path>
```

`session update`, `session close`, `draft write-request`, and `bundle
commit-page` all acquire the session lock implicitly and exit `2` with a
structured `{"error": "lock_held", "owner": "...", "acquired_at": "..."}`
payload on stderr if another owner holds it.

### KG

```
wikify kg seeds --session <path>                       # seed chunk ids (token-light)
wikify kg seeds --session <path> --persist             # also write the seeds onto session
wikify kg abstracts --corpus <path> --doc-ids '["doc_1","doc_2"]'
wikify kg evidence --session <path> --page-id "Atomic Layer Deposition" --top-k 8
```

`wikify kg seeds --persist` acquires the session lock, writes
`seed_doc_ids` and `seed_chunk_ids` onto the session, and is the
convention callers use when they expect those fields to appear in the
final `_run.json` snapshot.

### Draft / validate / bundle

```
wikify draft write-request \
    --session <path> --page-id "<id>" \
    --chunk-ids '["chunk_1","chunk_2","chunk_3"]'
    # --chunk-ids is REQUIRED. Typical callers pipe it from `wikify kg evidence`.

wikify validate write \
    --draft <scratch>/draft-<id>.json --response <scratch>/response-<id>.json \
    --session <path>
    # --session is REQUIRED for the skill workflow: on ok=true it transitions
    # session.pages[<id>].status from drafted to validated under the lock.

wikify bundle commit-page \
    --session <path> --response <scratch>/response-<id>.json \
    --validation <scratch>/validation-<id>.json
    # --validation is REQUIRED. commit-page verifies the verdict's ok=true
    # AND the session page entry is status=validated before writing the
    # page file and rebuilding <bundle>/_index.json / _wiki_graph.json
    # under the session lock.
```

### Render / eval / ingest

Unchanged from current CLI. Kept as stable entry points.

```
wikify ingest <source>
wikify html <bundle>
wikify eval <bundle>
```

## Composability

The agent may freely combine `wikify` commands with `Bash` shell tools (`Glob`,
`Read`, `jq`, `grep`). Prefer:

- plain JSON for single records
- newline-delimited JSON (`jsonl`) for lists
- file paths in output over embedded content

The agent should not pipe large output between commands through stdout. Pass
paths.

## Failure modes

- Schema violation — non-zero exit, structured error, no partial write.
- Session inconsistency (missing field, stage out of order) — non-zero exit, first-class error.
- Lock held — non-zero exit, `lock_held` error code with owner metadata.
- Output too large for stdout — write to file, return path (exit 0).
- Empty result — exit 0 with empty array or count 0; not an error.
