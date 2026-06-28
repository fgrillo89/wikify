---
name: query
description: Query workflow for answering from the committed Wikify wiki, falling back to corpus search when needed, and recording bundle feedback for refinement. Use when answer sufficiency, corpus fallback, and feedback policies are supplied.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_traverse mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse
---

# query

Answer a user question from committed wiki pages. When the wiki is
insufficient, search the corpus for grounded facts and append structured
query feedback so `refine` can improve the wiki later.

This workflow owns answer sufficiency, corpus fallback, and feedback
emission policy. Run it only with explicit sufficiency and feedback rules.

## Bind the session

Bind the bundle and corpus on the MCP session before any read:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
```

## Loop

1. Find and inspect relevant committed pages with `search-wiki`
   (`wiki_find` / `wiki_show` / `wiki_traverse`).
2. Judge whether the wiki evidence is sufficient against the supplied
   sufficiency rubric.
3. If insufficient, retrieve source evidence for the missing claims with
   `search-corpus` (`corpus_find` / `corpus_show` / `corpus_traverse`).
4. Answer with only supported claims. Ground every factual claim in cited
   page or chunk evidence; if evidence is still insufficient, say so
   plainly. See `references/answer-synthesis.md`.
5. When corpus fallback exposes a wiki gap, append query feedback with
   `bundle` (bash bundle mutation) so `refine` can close it.

## Data tables are not wiki pages

`kind=data` artifacts (comparison tables) are committed through the `data`
CLI into a separate store, not into `wiki.db`. They render and appear in
navigation but are **not** wiki-graph nodes. A `page_not_found` from
`wiki_show` / `wiki_traverse` / `wiki_find` on a data table is expected,
not an error to retry on the wiki side. Fall back to the `data` CLI:

```bash
wikify data list-artifacts --run <bundle>
wikify data query --subject "<subject>" --run <bundle> --format json
wikify data show <claim_id> --run <bundle>
```

## Strategy owned here

- Answer sufficiency rubric.
- Corpus fallback threshold.
- Query feedback severity.
- Synthesis tier.

## References

- `references/answer-synthesis.md`
- `../wikify/subskills/search-wiki/SKILL.md`
- `../wikify/subskills/search-corpus/SKILL.md`
- `../wikify/subskills/bundle/SKILL.md`
