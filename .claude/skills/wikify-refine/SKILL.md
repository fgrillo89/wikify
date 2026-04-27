---
name: wikify-refine
description: Drain the work inbox into evidence and merges, then re-write any concept marked `needs_refine`. Use after a `wikify-baseline` pass plus query traffic that produced `query_feedback` records. Status: stub — composition shape only.
allowed-tools: Bash(wikify *) Skill(wikify-*) Task
---

# wikify-refine (stub)

Status: stub — composition shape only. No Python orchestration; the
agent runs primitives in order.

## Intent

Apply accumulated inbox feedback (evidence suggestions, concept
suggestions, merge suggestions, query feedback) and re-write the
concepts that became `needs_refine` as a result.

## Composition

1. `wikify-work` — `work list inbox` to enumerate pending
   suggestions; `work tend` to consolidate them deterministically
   (this is what flips the `needs_refine` flag).
2. `wikify-work` — `work list --status needs_refine` (or grep the
   `work/index.md`) to find the concepts to refine.
3. For each concept (parallel up to N):
   - `wikify-work` — `work claim <slug>`.
   - `wikify-draft` — `draft build <slug> --task refine`.
   - Fork writer subagent — produce a refined `response.json`.
   - `wikify-draft` — `draft check <slug>`.
   - `wikify-wiki` — `wiki commit <slug>`.
   - `wikify-work` — `work release <slug>`.
4. `wikify-wiki` — `wiki build indexes/graph/vectors` after the
   batch finishes.

## Strategy

- Per-batch concurrency, retry policy, and tier escalation are
  decided here in skill markdown.
- No new CLI commands.

## References

- [atoms.md](../wikify/references/atoms.md)
- [escalation.md](../wikify/references/escalation.md)
- [write-constraints.md](../wikify/references/write-constraints.md)
