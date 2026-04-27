---
name: wikify-search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, seed documents, semantic search, text search, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
allowed-tools: Bash(wikify corpus *)
---

# wikify-search-corpus

Use this skill to inspect and search an existing corpus. It teaches the
available corpus read operations and traversal patterns; it does not
decide what to explore next.

## Capability Surface

- List corpus handles: docs, chunks, files, authors, figures, equations
  when exposed by the CLI.
- Show one handle with compact output by default and `--full` only when
  the workflow needs the full text.
- Run semantic search for topic, concept, and evidence queries.
- Run exact text search for phrases, acronyms, section labels,
  equations, and citation markers.
- Find seed documents through the CLI's centrality/PageRank seed
  selection.
- Traverse graph affordances exposed through corpus handles and flags:
  cited-by, cites, neighbors, authored-by, coauthor/source
  neighborhoods, section-scoped chunks, nearby figures, and nearby
  equations.

## Default Loop

1. Start with a small query or listing.
2. Inspect returned handles and previews.
3. Pick one or more handles.
4. Traverse one hop or show one selected handle.
5. Narrow or broaden based on the result.
6. Pull full text only after choosing the handle.

## Examples

```bash
wikify corpus find --seed --corpus <corpus> --max 12 --pagerank-weight 0.7
wikify corpus find "atomic layer deposition" --corpus <corpus> --top-k 8
wikify corpus find "HfO2" --corpus <corpus> --text
wikify corpus show chunk:<chunk-id> --corpus <corpus> --full
wikify corpus show doc:<doc-id> --corpus <corpus> --full
```

If the CLI exposes graph flags for the active branch, prefer recursive
handle traversal over one broad query. Inspect a small result, choose a
source/chunk/author handle, then traverse from that handle.

## Does Not Do

- Does not mutate a bundle.
- Does not add concepts or evidence.
- Does not choose an exploration strategy.
- Does not decide whether evidence is sufficient for writing.

## References

- `references/corpus-cli-patterns.md` - corpus command grammar and use
  cases.
- `references/corpus-recursive-search.md` - recursive search loops.
- `references/corpus-graph-traversals.md` - traversal examples and
  fallback patterns.
- `../wikify/references/cli/grammar.md` - shared CLI grammar.
- `../wikify/references/cli/output-contract.md` - output conventions.
