---
name: wikify-search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, seed documents, semantic search, text search, paper-level ranking by citation count, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
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
- Use **short handles** (`doc:<12-hex>`, `chunk:<8-hex>`) end to end —
  the CLI emits them in `--format quiet` and accepts them everywhere
  full ids are accepted.
- Run semantic search for topic, concept, and evidence queries.
- Aggregate chunk hits to the paper level with `--by paper`, and
  re-rank via `--rank citation_count|pagerank` to answer "most cited
  paper that talks about X" in one call.
- Run exact text search for phrases, acronyms, section labels,
  equations, and citation markers.
- Open `wikify corpus repl --corpus <corpus>` for iterative search so
  docs/chunks stay indexed and the semantic embedder is loaded only
  once per session.
- Find seed documents through the CLI's centrality/PageRank seed
  selection.
- Traverse one graph hop with `wikify corpus traverse <handle> --to
  <relation>`. Doc relations: `cited-by`, `references`, `chunks`,
  `figures`, `equations`, `authors`. Chunk relations: `source`,
  `cited-in-corpus`, `figures`, `equations`. Author relations:
  `sources`, `coauthors`. Output is handles — pipes directly into
  another `traverse` or `show`.
- For figures, the compact output includes the corpus-relative path,
  which the agent can pass directly to the Read tool for visual
  ingestion (PNG/JPG decoded by the multimodal model) or use to
  compose `![caption](path)` markdown links.
- Author handles are `author:first_last`. Find authors by topic
  (`find "<query>" --by author`), by global metric
  (`find --by author --rank h_index`), or via paper authorship
  (`traverse doc:<short> --to authors`). Compose multi-hop queries
  like "papers of authors who cite this paper" with shell pipes.
- `wikify corpus schema` self-describes the surface (node types, edge
  kinds, relations, metrics). `--explain` on `find` / `traverse`
  prints the resolved fluent-chain pseudocode without executing.
- `--corpus` is optional: explicit flag, `WIKIFY_CORPUS` env, or
  walk-up-from-cwd resolves it. One env export removes the flag from
  every command in the session.

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
wikify corpus find "atomic layer deposition" --by paper --rank citation_count \
    --top-k 3 --corpus <corpus>
wikify corpus find "HfO2" --corpus <corpus> --text
wikify corpus show chunk:<short> --corpus <corpus> --full
wikify corpus show doc:<short> --corpus <corpus> --full
wikify corpus traverse doc:<short> --to cited-by --rank citation_count \
    --top-k 5 --corpus <corpus>
wikify corpus traverse chunk:<short> --to cited-in-corpus \
    --rank citation_count --corpus <corpus>
wikify corpus traverse doc:<short> --to figures --corpus <corpus>
wikify corpus show figure:<short>/<stem> --corpus <corpus>
wikify corpus traverse doc:<short> --to equations --top-k 5 --corpus <corpus>
wikify corpus repl --corpus <corpus>
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
