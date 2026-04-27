---
name: wikify-render-eval
description: Re-render the static site and re-run eval against an already-committed Wikify bundle. Use as a snapshot step at the end of a workflow, or to compare metrics across runs over the same corpus. Status: stub — composition shape only.
allowed-tools: Bash(wikify *) Skill(wikify-*)
---

# wikify-render-eval (stub)

Status: stub — composition shape only. No Python orchestration; the
agent runs primitives in order.

## Intent

Produce a fresh static site and a fresh eval report from an
already-committed wiki. Useful as a final step in any workflow, or
when comparing two bundles over the same corpus.

## Composition

1. `wikify-wiki` — `wiki build indexes`, `wiki build graph`, `wiki
   build vectors` to refresh the projections under `derived/`.
2. `wikify-render` — `render --bundle <b> --format html` to write
   `derived/site/`.
3. `wikify-eval` — `eval --bundle <b> --corpus <c>` to write
   `derived/eval.json` (corpus is required for M1 / M6 / GT-C).

## Strategy

- Whether to re-build projections, or trust an already-fresh
  `derived/`, is decided here in skill markdown.
- No new CLI commands.

## References

- [schemas.md](../wikify/references/schemas.md)
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md)
