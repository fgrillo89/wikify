---
name: wikify-query
description: Query workflow for answering from the committed Wikify wiki, falling back to corpus search when needed, and recording bundle feedback for later refinement. Status is stub composition only.
allowed-tools: Bash(wikify *) Task
---

# wikify-query

Status: stub composition only. This workflow owns answer sufficiency,
corpus fallback, and feedback emission policy.

## Intent

Answer a user question from committed wiki pages when possible. If the
wiki is insufficient, search the corpus for grounded facts and append
structured query feedback so `wikify-refine` can improve the wiki later.

## Composition

1. Use `wikify-search-wiki` to find and inspect relevant committed
   pages.
2. Decide whether the wiki evidence is sufficient.
3. If insufficient, use `wikify-search-corpus` to retrieve source
   evidence for missing claims.
4. Answer with only supported claims.
5. Use `wikify-bundle` to append query feedback for missing coverage.

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
