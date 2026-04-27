---
name: wikify
description: Project-wide reference for the Wikify pipeline. Loads schemas, CLI grammar, citation format, write constraints, tier mapping, escalation policy, and graph APIs. Use when working on Wikify code, skills, prompts, bundles, or wiki output.
allowed-tools: Bash(wikify *)
---

# Wikify (umbrella)

Shared reference skill for the Wikify pipeline. Other skills link into
the files under `references/` instead of duplicating content. Strategy
decisions (loop shape, model tier, budget allocation, stopping
criteria, evidence top-k) live in skill markdown and the agent
prompt — never in Python defaults.

## CLI nouns

Seven top-level nouns. One sentence each:

- `corpus`   — build, refresh, query the input corpus (read-only during a wiki run).
- `run`      — bundle execution control: state, events, lock, lifecycle.
- `work`     — in-flight build state: concepts, evidence, claims, inbox, tend.
- `draft`    — per-attempt artifacts: build the WriteRequest, validate the WriteResponse.
- `wiki`     — the committed wiki layer: list/find/show/build/check/commit.
- `render`   — static HTML site generator (`--bundle`).
- `eval`     — metrics over the committed wiki (`--bundle` plus optional `--corpus`).

Full grammar: `references/cli-tool-surface.md`.

## When to use which workflow

- `wikify-baseline` — single linear pass over a fresh corpus: extract
  seed concepts, gather evidence, write each page once, commit, render,
  eval. Default workflow when growing a wiki from scratch.
- `wikify-query` — answer a question against a committed wiki (with
  corpus fallback) and append `query_feedback` to the inbox. Stub.
- `wikify-guided-explore` — model-driven loop that decides each turn
  whether to extract more concepts or gather more evidence. Stub.
- `wikify-refine` — drain the inbox into evidence and merges, then
  re-write any concept marked `needs_refine`. Stub.
- `wikify-render-eval` — re-render a static site and re-run eval against
  an already-committed wiki. Stub.
- `wikify-ingest` — wrapper around `wikify corpus build` /
  `wikify corpus refresh` for a fresh source dir. Stub.
- `wikify-maintain` — periodic `work tend` + inbox consolidation +
  refine-as-needed. Stub.

## Atomic skills

Atomic skills wrap the deterministic CLI nouns. Each is its own
discoverable skill directory:

- `wikify-corpus`  — corpus build + read-only queries.
- `wikify-run`     — run-level state, events, lock, close.
- `wikify-work`    — in-flight concepts, evidence, claims, inbox, tend.
- `wikify-draft`   — per-attempt draft IO + validation gate.
- `wikify-wiki`    — committed pages + projections + commit gate.
- `wikify-render`  — static site generation.
- `wikify-eval`    — metrics + report generation.

## Bundle layout

```
<bundle>/
  run/
    state.json
    events.jsonl
    lock
    io/<event_id>.{stdin,stdout,stderr}.txt
  work/
    index.md
    inbox/{evidence,concept,merge,query_feedback}_suggestions.jsonl
    concepts/<slug>/
      work.md
      evidence.jsonl
      .claim
      draft.json        (transient; gc'd post-commit)
      response.json     (transient)
      validation.json   (transient)
  wiki/
    articles/<slug>.md
    people/<slug>.md
    index.md
  derived/
    index.json
    graph.json
    vectors.npz
    eval.json           (default output of `wikify eval`)
    site/               (default output of `wikify render`)
```

Deep schema: `references/schemas.md`.

## Where state lives

- `run/state.json` — durable run identity, strategy label, budget,
  stage status. Mutated under `run/lock`.
- `run/events.jsonl` — append-only event ledger. The source of truth
  for cost rollup, telemetry parity, and trace replay.
- `work/concepts/<slug>/` — per-concept on-disk state. Mutated under
  the concept's `.claim` file (TTL-driven advisory lock).
- `wiki/` — committed pages. Promoted by `wiki commit` under the run
  lock; immutable until refined.
- `derived/` — projections: index, graph, vectors, eval report,
  rendered site. Cheap to rebuild from `wiki/`.

## Logging contract

Every CLI invocation under a bundle context appends a `cli_invoked`
event to `run/events.jsonl` capturing argv, cwd, exit code, duration,
and stdout/stderr previews. Heavy IO spills to
`run/io/<event_id>.{stdin,stdout,stderr}.txt`. Other event types:
`stage_changed`, `concept_created`, `evidence_added`, `draft_created`,
`call`, `validation_completed`, `page_committed`, `page_refined`,
`inbox_suggestion_created`, `inbox_consolidated`, `query_feedback_created`,
`budget_exceeded`, `run_closed`. Cost is computed from the `call`
events (`cost_summary(bundle)`).

## Strategy lives in skill markdown

The Python primitives never default a model tier, model id, evidence
top-k, seed budget, or loop shape. Every skill that calls a writer
(or any model) declares its tier and model id explicitly. Examples:

- `wikify draft build <slug> --task create --tier M --model-id claude-sonnet-4-6`
- `wikify corpus find --seed --max 12 --pagerank-weight 0.7`
- `wikify work claim <slug> --ttl-seconds 1800`

If a workflow needs to tune those numbers, it edits its own SKILL.md.

## Exit codes

- 0 success
- 1 validation / precondition failure
- 2 lock or claim held
- 3 budget exceeded
- 4 stale claim broken by `work tend`

## Reference index

- [atoms.md](references/atoms.md) — pre/post-conditions of every CLI atom.
- [cli-tool-surface.md](references/cli-tool-surface.md) — full CLI grammar.
- [schemas.md](references/schemas.md) — durable artifacts and `schema_version` policy.
- [tiers.md](references/tiers.md) — S/M/L tier mapping.
- [escalation.md](references/escalation.md) — when to escalate to tier L.
- [write-constraints.md](references/write-constraints.md) — Wikipedia voice + structural rules.
- [citation-format.md](references/citation-format.md) — `[^eN]` markers and reference definitions.
- [knowledge-graph.md](references/knowledge-graph.md) — fluent corpus KG API.
- [wiki-graph.md](references/wiki-graph.md) — fluent wiki KG API.
