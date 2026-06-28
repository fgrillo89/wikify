---
name: search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, sampling diverse entry points, semantic search, text search, paper-level ranking by citation count, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
allowed-tools: Bash(wikify corpus *) mcp__wikify__context_show mcp__wikify__context_set mcp__wikify__corpus_find mcp__wikify__corpus_traverse mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_schema mcp__wikify__corpus_image mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk
---

# search-corpus

Inspect and search an existing corpus. This skill teaches the available
read operations and traversal patterns. It does not decide what to
explore next.

## Surface choice: MCP is primary, bash CLI is fallback

The wikify MCP server (`mcp__wikify__corpus_*`) is the canonical
read/search surface. The MCP session keeps the embedder and vector
index loaded across calls; bash `wikify corpus *` re-pays a ~3.6 s
cold start per invocation (CLI startup + fastembed model load). Use the
bash CLI only when:

- the MCP server is not bound in the current environment;
- you are debugging the underlying CLI behaviour;
- you need a pipeline that streams handles into other shell tools
  (e.g. the `xargs` 3-hop walk in the cheatsheet).

High-leverage MCP params:

- `corpus_show(handle="doc:<short>", include_text=True, mode="full")` —
  whole body as one string. `mode="full"` only reshapes the response;
  `include_text=True` is what loads the body.
- `corpus_find(by="paper", field="title")` — literal title substring.
- `corpus_traverse(handle="chunk:...", to="cited-in-corpus")` — follow
  in-text citation markers.
- `corpus_image(handle="figure:...")` — return the binary image.

MCP setup, the full tool map, resource list, and fallback rules:
`../reference/references/mcp/setup.md`,
`../reference/references/mcp/tool-map.md`,
`../reference/references/mcp/resources.md`,
`../reference/references/mcp/fallback.md`.

## Step 0: discover the surface

Run once per session:

```
mcp__wikify__corpus_schema()
```

Returns node types, edge kinds, traverse relations grouped by handle
kind, and rank metrics. This is the index of what is available — return
to it instead of grepping source.

Bash equivalent: `wikify corpus schema`. Append `--explain` to any bash
`find` or `traverse` to print the resolved fluent-chain pseudocode
without executing.

## Step 1: resolve the corpus + set agent-friendly defaults

For MCP, bind once per session:

```
mcp__wikify__context_set(corpus_path="<path>")
mcp__wikify__context_show()
```

`context_show` reports doc/chunk counts, derived artifacts, and detected
field — equivalent to `wikify corpus check`.

For bash, `--corpus` is optional. Resolution order:

1. Explicit `--corpus <path>` flag.
2. `WIKIFY_CORPUS` environment variable.
3. Walk up from cwd for a directory with `manifest.json` and `docs/`.

Set both env vars at session start when using bash. Without
`WIKIFY_CLI_FORMAT`, `--format auto` resolves to `quiet` for non-TTY
callers (every agent shell), so every `find` / `traverse` returns bare
handles with no titles, scores, or citation counts:

```bash
export WIKIFY_CORPUS=data/corpora/<my-corpus>
export WIKIFY_CLI_FORMAT=compact   # rich rows by default; pipes still work
```

Bash examples below omit `--corpus`, `--format`, and stderr noise.

Embedder banners are silent by default. Set `WIKIFY_EMBED_VERBOSE=1`
when debugging GPU-provider fallback or model loading.

## Step 2: cheatsheet — most common questions

MCP signatures shown; bash equivalents in parentheses where they differ
meaningfully.

| Question                                            | MCP call (bash fallback) |
|-----------------------------------------------------|---------|
| **Search & ranking**                                | |
| Most-cited paper in corpus                          | `corpus_find(by="paper", rank="citation_count", top_k=10)` |
| Most central paper (PageRank)                       | `corpus_find(by="paper", rank="pagerank", top_k=10)` |
| Most-cited paper that talks about X                 | `corpus_find(query="X", by="paper", rank="citation_count")` |
| Paper whose **title** mentions X                    | `corpus_find(query="X", by="paper", field="title")` |
| Most-relevant chunks for X                          | `corpus_find(query="X", top_k=8)` |
| **Unsure which mode?** Semantic + BM25 + text       | `corpus_find(query="X", rank="all", top_k=12)` |
| Scope chunk search to one doc                       | `corpus_find(query="X", in_doc="<doc-handle>")` |
| Literal phrase / acronym / formula                  | `corpus_find(query="X", text=True)` |
| Diverse corpus entry points (PageRank + coverage)   | `corpus_sample(max_docs=12)` |
| **Authors**                                         | |
| Most-cited author                                   | `corpus_find(by="author", rank="citation_count")` |
| Most-prolific author                                | `corpus_find(by="author", rank="n_papers")` |
| Highest-impact author (h-index)                     | `corpus_find(by="author", rank="h_index")` |
| Authors writing on X                                | `corpus_find(query="X", by="author")` |
| Author profile (h, cites, coauthors)                | `corpus_show(handle="author:<key>")` |
| Papers by an author                                 | `corpus_traverse(handle="author:<key>", to="sources", rank="citation_count")` |
| Co-authors of an author                             | `corpus_traverse(handle="author:<key>", to="coauthors", rank="h_index")` |
| Authors of a paper                                  | `corpus_traverse(handle="doc:<short>", to="authors", rank="h_index")` |
| **Citations**                                       | |
| Papers citing this paper                            | `corpus_traverse(handle="doc:<short>", to="cited-by")` |
| Most-cited papers citing this paper                 | `corpus_traverse(handle="doc:<short>", to="cited-by", rank="citation_count")` |
| Bibliography of this paper (in-corpus targets)      | `corpus_traverse(handle="doc:<short>", to="references")` |
| In-corpus refs marked inside a chunk's text         | `corpus_traverse(handle="chunk:<short>", to="cited-in-corpus")` |
| **Concept-grounded recursive citation walk**        | `corpus_citation_walk(query="<concept>", depth=2, top_k=5)` |
| **Cosine-neighbour walk (semantic exploration)**    | `corpus_similarity_walk(query="<concept>", depth=2, neighbors=3)` |
| Walk from a specific chunk                          | `corpus_similarity_walk(from_chunk="chunk:<short>", depth=2)` |
| Papers by authors who cite this paper (3-hop pipe)  | bash CLI: `traverse doc:X --to cited-by --format quiet \| xargs -I {} traverse {} --to authors --format quiet \| sort -u \| xargs -I {} traverse {} --to sources --format quiet \| sort -u` |
| **Structure & media**                               | |
| Chunks of a paper                                   | `corpus_traverse(handle="doc:<short>", to="chunks")` |
| Figures of a paper                                  | `corpus_traverse(handle="doc:<short>", to="figures")` |
| Figures discussed near a chunk                      | `corpus_traverse(handle="chunk:<short>", to="figures")` |
| Equations of a paper                                | `corpus_traverse(handle="doc:<short>", to="equations")` |
| One figure's metadata + on-disk path                | `corpus_show(handle="figure:<short>/<stem>")` |
| Read a figure visually                              | `corpus_image(handle="figure:...")` returns the binary; or `corpus_show` then pass the printed `path` to the Read tool |
| One equation's LaTeX                                | `corpus_show(handle="equation:<short>")` |
| **Inspection**                                      | |
| Paper metadata (title, year, authors, ...)          | `corpus_show(handle="doc:<short>")` |
| Full chunk text                                     | `corpus_show(handle="chunk:<short>", full=True)` |
| Corpus health (doc/chunk counts, derived artifacts) | `context_show()` (bash: `corpus check`) |
| List all docs                                       | bash CLI: `list docs` |
| Chunks of a specific doc                            | bash CLI: `list chunks --doc <doc-id>` |
| **Self-discovery**                                  | |
| Learn the full surface                              | `corpus_schema()` |
| Preview what a bash command would do                | append `--explain` to bash `find` / `traverse` |

For bash, `--format quiet` prints handles only — pipe-safe and the
default when stdout is piped. MCP responses are structured JSON; no
format flag.

## Picking a `rank` for chunk search

`rank="all"` (MCP) / `--rank all` (bash) is the safe default — fans out
to semantic + bm25 + literal substring, RRF-fuses, dedupes, and tags
each row `via=sbt` (all three modes agreed). It tolerates a failure in
any single mode. Use `rank="bm25"` for exact terms / acronyms (sub-ms);
`rank="hybrid"` to weight a paraphrasable concept; `rank="semantic"`
for pure paraphrase.

FTS5 gotchas (bm25/hybrid only, not `all`): default-AND, so `"what is
X"` needs every word; `-` parses as NOT at query time, so quote
hyphenated phrases as `'"self-limiting"'`. See
`references/corpus-cli-patterns.md`.

## Recipes (deep dives in references/)

- **Walks**: `corpus_citation_walk` follows author-asserted [N] markers
  (sparse in low-citation corpora); `corpus_similarity_walk` follows
  cosine neighbours of chunk vectors (dense, ~180 ms at depth=2 on 5k
  chunks). Both return `{seeds, edges, chunks}`. Use citation-walk to
  trace an argument to its source; similarity-walk to explore a
  conceptual neighbourhood. Scope a chunk search to one paper with
  `corpus_find(query="X", in_doc="<doc-handle>")`.
- **Definition hunting**: try in order `'"<term> (<ABBREV>)"'`,
  `'"<ABBREV> is"'`, then `'"<term>"'`, all with `rank="bm25"`. Skip
  `'"<full term> is"'` — authors switch to the abbrev after
  introduction. Full recipe in `references/corpus-cli-patterns.md`.
- **Empty query + `rank=<metric>`** ranks the whole population.
  `by="paper"` for source-typed metrics, `by="author"` for
  author-typed. With a query, `rank=<metric>` re-orders the semantic
  top-K — "most cited paper that discusses X".

Read `context_show().health.available_metrics` to see which metrics are
populated for the current corpus before choosing a rank.

## Default loop

Small query -> inspect handles + previews -> pick -> traverse one hop or
show -> narrow or broaden -> only then pull full text.

## Does not do

Mutate a bundle, pick an exploration strategy, or decide whether
evidence is sufficient for writing.

## References

- `references/corpus-cli-patterns.md` — full grammar, format columns,
  handle rules, environment variables, complete worked examples.
- `references/corpus-recursive-search.md` — multi-hop pipelines.
- `references/corpus-graph-traversals.md` — relation catalogue + recipes.
- `../reference/references/cli/grammar.md` — shared CLI grammar.
- `../reference/references/cli/output-contract.md` — output conventions.
