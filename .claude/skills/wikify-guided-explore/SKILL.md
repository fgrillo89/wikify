---
name: wikify-guided-explore
description: Model-guided Wikify exploration workflow that composes corpus search, wiki search, bundle operations, concept extraction references, and page writing. Use for iterative exploration where the model chooses the next action and explicit budget, stop, and retry policies are supplied.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-guided-explore

This workflow owns the exploration policy; core skills own mechanics.
Run it only with explicit budget, stop, retry, and concurrency settings.

## Intent

Iteratively inspect bundle state, choose the largest gap, and dispatch
one action: search corpus, inspect wiki, extract concepts from selected
text, gather evidence, write a ready page, or tend work state.

## Composition

Bind the bundle + corpus on the MCP session before the first
iteration: `mcp__wikify__context_set(corpus_path=<corpus>,
bundle_path=<bundle>)`. Read/search uses MCP throughout; bundle
mutations stay on bash CLI.

Each iteration:

1. Use `wikify-bundle` (bash) to inspect work dashboard, claims,
   recent events, and budget status.
2. Decide the next action using the workflow rubric.
3. Use one or more core skills:
   - `wikify-search-corpus` (MCP) for corpus probes and graph traversal.
   - `wikify-search-wiki` (MCP) for committed coverage checks.
   - `wikify-bundle` (bash) for add concept, add evidence, claim, tend,
     validate, commit, and projection mechanics.
   - `wikify-gather-evidence` (sonnet subagents, MCP-backed) when a
     selected concept needs grounded evidence; spawn in parallel
     waves grouped by cluster.
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
