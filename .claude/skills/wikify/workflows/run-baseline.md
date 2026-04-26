---
name: wikify-baseline
description: Baseline workflow loop — extract concepts from corpus seeds, gather evidence, write each page once, commit. Use when the user asks to build a wiki, run a baseline pass, or grow a v2 bundle from a corpus.
allowed-tools: Bash(wikify *) Task
---

# Baseline workflow

Linear, single-pass over a corpus. One write per concept. No re-entry
into ``extract-concepts`` after the initial seed pass. Strategy
defaults this skill chooses are documented inline; if you want to
override (smaller seed budget, different tier mapping, etc.) edit the
skill.

## Prerequisites

- A v2 bundle has been initialised: ``wikify run init --bundle <b>
  --corpus <c> --strategy baseline``.
- The corpus has been built and is reachable through ``--corpus``.

## Loop

### 1. Seed the corpus

```
wikify corpus find --seed --corpus <c> --max 12 --pagerank-weight 0.7
```

Returns up to 12 doc ids in score order.

### 2. Extract concepts (once per seed doc)

For each seed doc, fork a tier-S subagent that reads
``corpus show doc:<id>`` and produces an ``ExtractResponse`` JSON.
Persist via:

```
wikify work add concept "<title>" --kind article|person --aliases '["..."]'
```

The subagent picks the title (from the doc's abstract) and a
provisional kind.

### 3. Per concept (parallel up to 4 agents)

For each concept, in any order:

```
wikify work claim <slug> --ttl-seconds 1800
wikify corpus find "<concept title>" --corpus <c> --top-k 12 --format json \
  | <jsonl writer> > /tmp/ev.jsonl
wikify work add evidence <slug> --records /tmp/ev.jsonl
wikify draft build <slug> --task create --corpus <c> \
  --model-id claude-sonnet-4-6 --tier M
# ... fork a tier-M writer subagent, write response.json ...
wikify draft check <slug>
wikify wiki commit <slug>
wikify work release <slug>
```

If ``draft check`` exits non-zero, retry once (still tier M); if the
second attempt also fails, escalate to tier L per
``escalation.md``; on the third failure mark the concept ``failed``.

### 4. Tend

```
wikify work tend
```

Drains the inbox (cross-talk during write), regenerates
``work/index.md``, expires stale claims.

### 5. Close

```
wikify run close --status completed
```

## Stop conditions

- All seed concepts reach ``committed`` or ``failed``, OR
- ``budget_exceeded`` event observed in ``wikify run list events``, OR
- ``run set --target-haiku-eq`` is reached.

## What this workflow does NOT do

- It does not re-extract concepts mid-loop. Use ``wikify-guided`` for
  that.
- It does not refine pages from query feedback. Use
  ``wikify-maintain`` for that.
- It does not pre-rebuild ``derived/graph.json`` /
  ``derived/vectors.npz`` between commits. Run
  ``wikify wiki build graph`` and ``wikify wiki build vectors`` once
  at the end.
