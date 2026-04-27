---
name: wikify-guided-explore
description: Model-driven exploration loop over a Wikify bundle. Each turn, the model reads the work dashboard, identifies the largest gap, and dispatches the next primitive action (extract another concept, gather more evidence, write a thin page). Status: stub — composition shape only.
allowed-tools: Bash(wikify *) Skill(wikify-*) Task
---

# wikify-guided-explore (stub)

Status: stub — composition shape only. No Python orchestration; the
agent runs primitives in order.

## Intent

Where `wikify-baseline` is a single linear pass with a fixed seed
budget, `wikify-guided-explore` is a model-driven loop that decides
each turn whether to extract more concepts, gather more evidence on a
shallow concept, or write a page that has enough evidence.

## Composition

Each iteration:

1. `wikify-work` — `work list` and `work show <slug>` to read the
   current dashboard (concept count, evidence depth, claims).
2. Decision (model): pick the next primitive action.
3. Dispatch one of:
   - `wikify-corpus` — `corpus find` to discover a new concept or
     gather evidence on an existing one.
   - `wikify-work` — `work add concept` for a newly discovered
     concept, or `work add evidence` to deepen an existing one.
   - `wikify-draft` + `wikify-wiki` — write and commit a concept
     once its evidence depth crosses the per-page floor.
4. `wikify-work` — `work tend` periodically.

Stop when budget is exhausted (`budget_exceeded` event) or the
model decides coverage is sufficient.

## Strategy

- Per-iteration budget cap and concurrency are decided here in skill
  markdown.
- The decision rubric (what counts as "thin" vs "ready to write")
  lives here, not in Python.
- No new CLI commands.

## References

- [atoms.md](../wikify/references/atoms.md)
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md)
- [tiers.md](../wikify/references/tiers.md)
