---
name: wikify-refine
description: Refinement workflow that consolidates bundle inbox feedback, finds concepts marked needs_refine, gathers additional evidence when required, rewrites pages, validates, and commits replacements. Use when refinement threshold, batch, and retry policies are supplied.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse
---

# wikify-refine

This workflow owns refinement thresholds, batch policy, and
retry/escalation. Run it only with explicit target selection and retry
rules.

## Intent

Apply accumulated inbox feedback and improve committed pages through the
normal write, validate, and commit gate.

## Composition

Bind the bundle + corpus on the MCP session first:
`mcp__wikify__context_set(corpus_path=<corpus>, bundle_path=<bundle>)`.
Read/search uses MCP throughout; bundle mutations stay on bash CLI.

1. Use `wikify-bundle` (bash) to list inbox state and run `work tend`.
2. Use `wikify-bundle` (bash) to find concepts marked `needs_refine`.
3. For each target, optionally use `wikify-search-corpus` (MCP) to
   gather more evidence — or delegate to `wikify-gather-evidence`
   subagents for grounded vetting.
4. Use `wikify-bundle` (bash) to claim the concept and build a refine
   draft.
5. Use `wikify-write-page` with `refinement-style.md`.
6. Use `wikify-bundle` (bash) to validate, commit, release, and
   refresh projections.
7. Use `wikify-organize-wiki` after a committed batch of at least five
   pages, or once before final render if fewer pages changed.

## Strategy Owned Here

- Refinement threshold.
- Evidence growth policy.
- Batch concurrency.
- Retry and escalation policy.

## References

- `../wikify-bundle/SKILL.md`
- `../wikify-search-corpus/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify-write-page/references/refinement-style.md`
- `../wikify/references/writing/escalation.md`
