---
name: wikify-search-wiki
description: Use the committed-wiki MCP tools (or bash CLI as fallback) to search pages, traverse wiki relations, and bridge back to the corpus.
allowed-tools: Bash(wikify wiki *) mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__wiki_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__corpus_show
---

# wikify-search-wiki

Use this skill to inspect the committed wiki. It teaches the read/query
surface; it does not decide whether to refine, expand, or stop.

## Surface choice: MCP is primary, bash CLI is fallback

The wikify MCP server (`mcp__wikify__wiki_*`) is the canonical
read surface. The MCP session keeps the wiki index loaded across
calls. Bash `wikify wiki *` re-loads indexes per invocation. Use bash
only when the MCP server is not bound, when debugging the underlying
CLI, or when piping handles into shell tools.

Bind the bundle once per session:

```
mcp__wikify__context_set(bundle_path="<bundle>")
mcp__wikify__context_show()
```

## Capability Surface

- List committed pages with `wiki_find()` (empty query lists all).
- `wiki_show(handle="<title-or-slug>")` returns the page; pass
  `full=True` for the body. Handles accept the natural title or file
  slug, matched case- and separator-insensitively (so
  `"Atomic Layer Deposition"` resolves whether the file is
  `Atomic Layer Deposition.md` or `atomic-layer-deposition.md`), or a
  unique case-insensitive prefix.
- Search committed pages with `wiki_find(query="X", mode="hybrid")`.
  Modes: `hybrid` (default), `bm25`, `semantic`, `text`.
- `wiki_traverse(handle="<slug>", to="<relation>")` walks one wiki
  hop: `links` (outgoing), `linked-by` (incoming), `co-evidence`
  (pages sharing source docs), `similar`, `see-also`,
  `category` / `categories`, and `evidence` (emits `chunk:` handles
  for the corpus).
- Category handles support `children`, `parent`, `pages`.
- Bridge from a committed page back to corpus evidence by piping
  `wiki_traverse(..., to="evidence")` handles into
  `corpus_show(handle=...)`.

The bash CLI also exposes `wikify wiki repl --run <bundle>` for
iterative committed-page search; MCP achieves the same with a bound
context.

## Default Loop

1. Search or list pages.
2. Inspect a compact page result.
3. Show the selected page only when needed.
4. Inspect relationships, category context, or evidence handles.
5. Decide whether the next step is another wiki query, a corpus search,
   or a workflow-level bundle mutation.

## Examples (MCP)

```
mcp__wikify__wiki_find(query="")                      # list all
mcp__wikify__wiki_find(query="ALD vs CVD", top_k=5)
mcp__wikify__wiki_find(query="resistive switching", mode="semantic")
mcp__wikify__wiki_find(query="atomic layer deposition", mode="text")
mcp__wikify__wiki_show(handle="Atomic Layer Deposition", full=True)
mcp__wikify__wiki_traverse(handle="Atomic Layer Deposition",
                           to="links", top_k=10)
mcp__wikify__wiki_traverse(handle="Atomic Layer Deposition",
                           to="categories")
mcp__wikify__wiki_traverse(handle="category:materials", to="pages")
mcp__wikify__wiki_traverse(handle="Atomic Layer Deposition",
                           to="evidence")
# -> for each returned chunk:
mcp__wikify__corpus_show(handle="<chunk-handle>")
```

## Examples (bash fallback)

```bash
wikify wiki list --run <bundle>
wikify wiki find "ALD vs CVD" --run <bundle> --top-k 5
wikify wiki find "resistive switching" --mode semantic --run <bundle>
wikify wiki find "atomic layer deposition" --run <bundle> --text
wikify wiki show "Atomic Layer Deposition" --run <bundle> --full
wikify wiki traverse "Atomic Layer Deposition" --to links \
    --top-k 10 --run <bundle>
wikify wiki traverse "Atomic Layer Deposition" --to evidence \
    --format quiet --run <bundle> \
  | xargs -I {} wikify corpus show {} --corpus <corpus>
wikify wiki repl --run <bundle>
wikify wiki check --run <bundle>
```

## Does Not Do

- Does not append query feedback.
- Does not change concept status.
- Does not commit or refine pages.
- Does not decide whether wiki coverage is sufficient.

## References

- `references/wiki-cli-patterns.md` - committed wiki command grammar and
  use cases.
- `references/wiki-recursive-search.md` - recursive wiki search loops.
- `references/wiki-corpus-bridges.md` - moving from wiki evidence to
  corpus search.
- `../wikify/references/cli/grammar.md` - shared CLI grammar.
- `../wikify/references/cli/output-contract.md` - output conventions.
