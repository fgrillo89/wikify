---
name: wikify
description: Shared Wikify reference material for Claude Code skills. Use when working on Wikify skills, workflows, prompts, bundle state, writing rules, schemas, citation grounding, CLI grammar, or exploration patterns.
allowed-tools: Bash(wikify *)
---

# Wikify

This is the shared reference skill. It is not a workflow. It points
other skills at durable project facts and reusable prompt/reference
material.

## Canonical Skill Tree

`.claude/skills/` is the canonical Wikify skill tree. Do not maintain
parallel skill trees by hand; generate compatibility exports from this
tree when another runtime needs them.

## Core Capability Skills

- `wikify-search-corpus` - read/search the corpus CLI surface.
- `wikify-search-wiki` - read/search committed wiki pages.
- `wikify-write-page` - produce `WriteResponse` page prose from supplied
  context and evidence.
- `wikify-bundle` - inspect and mutate bundle state mechanically.

These skills expose capabilities. They do not decide exploration order,
budgets, readiness thresholds, model tiers, retries, or stop
conditions.

## Workflow Skills

Workflow skills own strategy. They compose the core skills and decide
what to inspect, when to write, how much to spend, how much parallelism
to use, and when to stop.

- `wikify-baseline` - baseline strategy over a fresh or active bundle.
- `wikify-guided-explore` - model-guided exploration loop.
- `wikify-query` - answer from wiki with corpus fallback and feedback.
- `wikify-refine` - refine committed pages from inbox/new evidence.

Only run workflow skills that define executable steps, inputs, and stop
conditions. Treat incomplete workflow outlines as design references
until those contracts are present.

## Reference Index

Bundle and state:

- `references/bundle/layout.md`
- `references/bundle/state.md`
- `references/bundle/events-ledger.md`
- `references/bundle/locking-and-claims.md`

CLI:

- `references/cli/grammar.md`
- `references/cli/output-contract.md`
- `references/cli/exit-codes.md`

Writing:

- `references/writing/schemas.md`
- `references/writing/citation-format.md`
- `references/writing/write-constraints.md`
- `references/writing/tiers.md`
- `references/writing/escalation.md`
- `references/writing/field-guides/generic.md`
- `references/writing/field-guides/<field>.md`

Exploration:

- `references/exploration/concept-extraction.md`
- `references/exploration/sampling-patterns.md`
- `references/exploration/workflow-contracts.md`

## Field Guide Rule

Writers always load the generic field guide. If corpus metadata,
workflow state, or field detection identifies one field with confidence,
load exactly one matching field guide in addition to generic. Do not
load all field guides.
