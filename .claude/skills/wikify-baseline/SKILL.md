---
name: wikify-baseline
description: Run the baseline Wikify workflow on a bundle. Use when the user asks to build a wiki, run a baseline pass, extract concepts, or grow a fresh bundle from a corpus. Linear single-pass: extract seeds, gather evidence, write each page once, commit, render, eval.
allowed-tools: Bash(wikify *) Task
---

# wikify-baseline

Single-pass baseline workflow over a corpus. One write per concept.
No re-entry into concept extraction after the initial seed pass. The
strategy decisions documented inline live in this skill markdown —
they are not silent Python defaults; they are choices the agent
makes before each run and may override by editing this skill.

## Strategy decisions (override here)

- Seed budget: `--max 12` for `corpus find --seed`.
- PageRank weight for seed selection: `--pagerank-weight 0.7`.
- Evidence top-k per concept: `--top-k 12` for `corpus find`.
- Writer tier and model: tier `M`, `--model-id claude-sonnet-4-6`.
- Extractor tier: tier `S` (haiku-class).
- Per-concept claim TTL: `--ttl-seconds 1800`.
- Concurrent writers: up to 4 (one per claimed concept).
- Retry policy: one same-tier retry on validation failure, then one
  escalation to tier `L`, then mark the concept `failed`. See
  [escalation.md](../wikify/references/escalation.md).
- Stop conditions: all seeded concepts reach `committed` or
  `failed`; or `budget_exceeded` event observed; or
  `target_haiku_eq` reached.

## Prerequisites

- A corpus has been built and is reachable through `--corpus`.
- A bundle has been initialised:
  ```
  wikify run init --bundle <b> --corpus <c> --strategy baseline
  ```

## Loop

### 0. Set bundle context

After `run init`, change directory into the bundle once. The remaining
commands resolve the bundle from the cwd's `run/state.json`, so
`--run <b>` is omitted below; the corpus path stays explicit because
the corpus is a separate artifact.

```
cd <b>
```

If you cannot `cd`, append `--run <b>` to every `work`, `draft`,
`wiki`, and `run` command, and `--bundle <b>` to `render`/`eval`.

### 1. Seed the corpus

```
wikify corpus find --seed --corpus <c> --max 12 --pagerank-weight 0.7
```

Returns up to 12 doc ids in score order.

### 2. Extract concepts (once per seed doc)

For each seed doc, fork a tier-S subagent (`Task` with model
haiku-class) that reads `wikify corpus show doc:<id> --corpus <c> --full`
and returns an `ExtractResponse` JSON. Persist each extracted concept
via:

```
wikify work add concept "<title>" --kind article|person --aliases '["..."]'
```

The subagent picks the title (from the doc's abstract or first
heading) and a provisional kind (`article` for concepts/methods,
`person` for biographical entries).

### 3. Per concept (parallel up to 4 writers)

For each concept slug, in any order, fork a tier-M subagent that runs
this sequence:

```
wikify work claim <slug> --ttl-seconds 1800
wikify corpus find "<concept title>" --corpus <c> --top-k 12 --format json \
  | <jsonl writer> > /tmp/ev.jsonl
wikify work add evidence <slug> --records /tmp/ev.jsonl
wikify draft build <slug> --task create --corpus <c> \
                         --model-id claude-sonnet-4-6 --tier M
# fork writer subagent; it reads draft.json and writes response.json
wikify draft check <slug>
wikify wiki commit <slug>
wikify work release <slug>
```

If `draft check` exits non-zero, retry once at tier M with a stricter
prompt; if the second attempt also fails, escalate to tier L per
[escalation.md](../wikify/references/escalation.md); on the third
failure mark the concept `failed` (`wikify work set <slug>
--status failed`).

### 4. Tend

```
wikify work tend
```

Drains the inbox (cross-talk during write), regenerates
`work/index.md`, expires stale claims.

### 5. Project, render, eval

```
wikify wiki build indexes
wikify wiki build graph
wikify wiki build vectors
wikify render --bundle <b> --format html
wikify eval   --bundle <b> --corpus <c>
```

Note: `render` and `eval` use `--bundle`, not `--run`.

### 6. Close

```
wikify run close --status completed
```

## Stop conditions

- All seed concepts reach `committed` or `failed`, OR
- `budget_exceeded` event observed in
  `wikify run list events --type budget_exceeded`, OR
- `wikify run set --target-haiku-eq` is reached.

## What this workflow does NOT do

- It does not re-extract concepts mid-loop. Use
  `wikify-guided-explore` for that.
- It does not refine pages from query feedback. Use `wikify-refine`
  for that.
- It does not pre-rebuild `derived/graph.json` or
  `derived/vectors.npz` between commits. Build them once at the end
  (step 5).

## References

- [atoms.md](../wikify/references/atoms.md) — atom contracts.
- [tiers.md](../wikify/references/tiers.md) — S/M/L mapping.
- [escalation.md](../wikify/references/escalation.md) — retry/escalate policy.
- [write-constraints.md](../wikify/references/write-constraints.md) —
  what the writer must produce.
- [citation-format.md](../wikify/references/citation-format.md) —
  the `[^eN]` grammar.
- [schemas.md](../wikify/references/schemas.md) — file contract.
