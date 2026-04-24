---
name: wikify/reference/atoms
description: Atomic operations a wikify workflow skill composes, with pre/post-conditions and the CLI command that realizes each.
---

# Atoms

A workflow skill composes these atomic operations. Each atom has documented
pre- and post-conditions and a single CLI command (or subagent invocation)
that realizes it. Skills should call these atoms in sequence; they should not
invent alternate deterministic logic inline.

## `seed-select`

Select the initial set of document-seeds to extract from, given a corpus and
a budget.

- **Pre**: session exists with `status=active`, `stages.seed_selection.status=pending`, corpus ingested.
- **Post**: session `stages.seed_selection.status=done`; selected chunk IDs written to a scratch list or patched into the session.
- **Realization**: `wikify kg seeds --session <path>` (deterministic; wraps the PageRank + submodular logic extracted from `baselines.pipeline._select_seeds`).

## `extract`

Extract concept and person entries from one chunk, returning a validated
`ExtractResponse`.

- **Pre**: session exists; a concrete `chunk_id` and its text are available on disk or directly passable.
- **Post**: a scratch `extract-<chunk_id>.json` exists with `{concepts: [...], tokens_in, tokens_out}`; each concept has a `[^eN]`-mappable `chunk_id` and a verbatim quote.
- **Realization**: Task subagent at tier S. The skill prompts the subagent with the chunk text plus the `ExtractResponse` schema and the content rules. Validation via `src/wikify/schema.py::ExtractResponse` before scratch-write.

## `retrieve-evidence`

Retrieve the top-K evidence chunks for a given page title.

- **Pre**: session exists; page id is a valid extracted-concept title; corpus vector store is available.
- **Post**: a list of `{chunk_id, doc_id, score, quote, page_title, summary}` entries suitable for inclusion in a `WriteRequest.evidence_v2`.
- **Realization**: `wikify kg evidence --session <path> --page-id <id> --top-k <K>`.

## `draft`

Build a `WriteRequest` payload from session state and evidence; write it to
scratch. The subagent then writes a `WriteResponse` against this request.

- **Pre**: session exists; page is in `status=planned`; evidence has been retrieved.
- **Post**: scratch `draft-<page_id>.json` exists with `WriteRequest.schema_version=1`; session `pages[i].status=drafted`, `draft_path` set.
- **Realization (part 1 — request building)**: `wikify draft write-request --session <path> --page-id <id>`.
- **Realization (part 2 — model call)**: Task subagent at tier M consumes the request, emits a `WriteResponse` JSON at `<scratch>/response-<page_id>.json`.

## `validate`

Run schema and structural checks on a `WriteResponse` scratch payload.

- **Pre**: scratch `draft-<page_id>.json` and `response-<page_id>.json` exist.
- **Post**: scratch `validation-<page_id>.json` exists with `{ok: bool, errors: [...]}`; on `ok=true`, session `pages[i].status=validated` and `validation_path` is set.
- **Realization**: `wikify validate write --draft <scratch>/draft-<id>.json --response <scratch>/response-<id>.json`. Wraps the `WriteResponse` Pydantic validators plus the `QuoteNotInChunkError` substring check.

On `ok=false`: one retry of `draft` part 2 at the same tier; then an escalation to tier L per `escalation.md`; then `pages[i].status=failed`.

## `commit-page`

Promote a validated `WriteResponse` to a canonical `pages/<id>.md` and update
indices.

- **Pre**: `validation-<page_id>.json` exists with `ok=true`; session `pages[i].status=validated`.
- **Post**: `<bundle>/pages/<id>.md` exists with YAML frontmatter; `<bundle>/_index.json` and `<bundle>/_wiki_graph.json` rebuilt; session `pages[i].status=committed`.
- **Realization**: `wikify bundle commit-page --session <path> --response <scratch>/response-<id>.json`.

## `checkpoint`

Snapshot the current session to `_session/checkpoints/<label>.json` so a later
agent can resume without guessing.

- **Pre**: session exists.
- **Post**: `<bundle>/_session/checkpoints/<label>.json` exists; session `updated_at` bumped.
- **Realization**: `wikify session checkpoint --session <path> --label <label>`.

When to checkpoint (baseline): after seed selection completes, after each
committed page, and before session close. Guided workflows checkpoint more
densely.

## Composition

A baseline iteration composes these atoms in this order:

```
seed-select
  -> (for each seed) extract (parallelizable across subagents)
     -> (for each extracted concept that survives dedup) retrieve-evidence
        -> draft
        -> subagent write (tier M)
        -> validate
        -> commit-page
        -> checkpoint (every N commits)
-> session close
```

Scripted and guided workflows compose the same atoms but in different loop
shapes and with explicit stopping criteria.
