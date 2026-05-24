---
name: wikify-query
description: Query workflow for answering from the committed Wikify wiki, falling back to corpus search when needed, and recording bundle feedback for refinement. Use when answer sufficiency, corpus fallback, and feedback policies are supplied.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_traverse mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse
---

# wikify-query

This workflow owns answer sufficiency, corpus fallback, and feedback
emission policy. Run it only with explicit sufficiency and feedback
rules.

## Intent

Answer a user question from committed wiki pages when possible. If the
wiki is insufficient, search the corpus for grounded facts and append
structured query feedback so `wikify-refine` can improve the wiki later.

## Composition

Bind the bundle and corpus on the MCP session first:
`mcp__wikify__context_set(corpus_path=<corpus>, bundle_path=<bundle>)`.

1. Use `wikify-search-wiki` (MCP `wiki_find` / `wiki_show` /
   `wiki_traverse`) to find and inspect relevant committed pages.
2. Decide whether the wiki evidence is sufficient.
3. If insufficient, use `wikify-search-corpus` (MCP `corpus_find` /
   `corpus_show` / `corpus_traverse`) to retrieve source evidence for
   missing claims.
4. Answer with only supported claims.
5. Use `wikify-bundle` to append query feedback for missing coverage
   (bash — bundle mutation).

## Strategy Owned Here

- Answer sufficiency rubric.
- Corpus fallback threshold.
- Query feedback severity.
- Synthesis tier.

## References

- `references/answer-synthesis.md`
- `../wikify-search-wiki/SKILL.md`
- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
