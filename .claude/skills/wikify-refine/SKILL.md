---
name: wikify-refine
description: Refinement workflow that consolidates bundle inbox feedback, finds concepts marked needs_refine, gathers additional evidence when required, rewrites pages, validates, and commits replacements. Status is stub composition only.
allowed-tools: Bash(wikify *) Task
---

# wikify-refine

Status: stub composition only. This workflow owns refinement thresholds,
batch policy, and retry/escalation.

## Intent

Apply accumulated inbox feedback and improve committed pages through the
normal write, validate, and commit gate.

## Composition

1. Use `wikify-bundle` to list inbox state and run `work tend`.
2. Use `wikify-bundle` to find concepts marked `needs_refine`.
3. For each target, optionally use `wikify-search-corpus` to gather more
   evidence.
4. Use `wikify-bundle` to claim the concept and build a refine draft.
5. Use `wikify-write-page` with `refinement-style.md`.
6. Use `wikify-bundle` to validate, commit, release, and refresh
   projections.

## Strategy Owned Here

- Refinement threshold.
- Evidence growth policy.
- Batch concurrency.
- Retry and escalation policy.

## References

- `../wikify-bundle/SKILL.md`
- `../wikify-search-corpus/SKILL.md`
- `../wikify-write-page/references/refinement-style.md`
- `../wikify/references/writing/escalation.md`
