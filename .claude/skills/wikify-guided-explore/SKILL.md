---
name: wikify-guided-explore
description: Model-guided Wikify exploration workflow that composes corpus search, wiki search, bundle operations, concept extraction references, and page writing. Use for iterative exploration where the model chooses the next action and explicit budget, stop, and retry policies are supplied.
allowed-tools: Bash(wikify *) Task
---

# wikify-guided-explore

This workflow owns the exploration policy; core skills own mechanics.
Run it only with explicit budget, stop, retry, and concurrency settings.

## Intent

Iteratively inspect bundle state, choose the largest gap, and dispatch
one action: search corpus, inspect wiki, extract concepts from selected
text, gather evidence, write a ready page, or tend work state.

## Composition

Each iteration:

1. Use `wikify-bundle` to inspect work dashboard, claims, recent events,
   and budget status.
2. Decide the next action using the workflow rubric.
3. Use one or more core skills:
   - `wikify-search-corpus` for corpus probes and graph traversal.
   - `wikify-search-wiki` for committed coverage checks.
   - `wikify-bundle` for add concept, add evidence, claim, tend,
     validate, commit, and projection mechanics.
   - `wikify-write-page` for writer subagents.
   - `wikify-organize-wiki` after each committed batch of at least five
     pages, or once before final render if fewer pages changed.
4. Stop on budget, coverage, or no useful next action.

## Strategy Owned Here

- Breadth/depth tradeoff.
- Thin versus ready-to-write thresholds.
- Sampling pattern selection.
- Per-iteration budget.
- Writer concurrency and retry policy.

## References

- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/workflow-contracts.md`
- `../wikify/references/writing/tiers.md`
