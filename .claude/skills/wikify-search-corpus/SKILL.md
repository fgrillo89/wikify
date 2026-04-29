---
name: wikify-search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, sampling diverse entry points, semantic search, text search, paper-level ranking by citation count, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
allowed-tools: Bash(wikify corpus *)
---

# wikify-search-corpus

Use this skill to inspect and search an existing corpus. It teaches the
available corpus read operations and traversal patterns; it does not
decide what to explore next.

## MCP mode

If `mcp__wikify__corpus_schema` is in the tool list, prefer MCP tools
over CLI verbs for repeated reads (`corpus_find`, `corpus_traverse`,
`corpus_show`, `corpus_sample`, `corpus_schema`, `context_show`).
Validation and data are identical — both adapters call the same
domain helpers.

High-leverage parameters worth knowing:

- `corpus_show(handle="doc:<short>", include_text=True, sections=["intro"])`
  returns the paper body in one call. Without `include_text`, the
  result still carries `meta.sections` and `abstract`.
- `corpus_find(by="paper", field="title")` does literal substring
  search on `Document.title` — use for "title mentions X".
- `corpus_traverse(handle="doc:...", to="chunks")` returns chunks in
  document order with `section_path` + `ord` on each row.

See `../wikify/references/mcp/{setup,tool-map,resources,fallback}.md`
for setup, the tool↔CLI map, URI patterns, and CLI fallback.

## Step 0: discover the surface

Run once per session:

```bash
wikify corpus schema
```

Prints node types, edge kinds, traverse relations grouped by handle
kind, and rank metrics. This is the **index** of what's available — go
back to it instead of grepping source.

Add `--explain` to any `find` or `traverse` invocation to see the
resolved fluent-chain pseudocode (e.g. `chunks().search('X', top_k=30)
.group_by_doc().resort_by('citation_count').take(3)`) without
executing. Useful when you're unsure what a flag combination will do.

## Step 1: resolve the corpus + set agent-friendly defaults

`--corpus` is optional. Resolution order:

1. Explicit `--corpus <path>` flag.
2. `WIKIFY_CORPUS` environment variable.
3. Walk up from cwd looking for a directory with `manifest.json` and `docs/`.

**Set both env vars at session start.** Without `WIKIFY_CLI_FORMAT`,
`--format auto` resolves to `quiet` for non-TTY callers (every agent
shell), so every `find` / `traverse` returns bare handles with no
titles, scores, or citation counts:

```bash
export WIKIFY_CORPUS=data/corpora/<my-corpus>
export WIKIFY_CLI_FORMAT=compact   # rich rows by default; pipes still work
```

Examples below omit `--corpus`, `--format`, and stderr noise.

Embedder banners are silent by default. Set `WIKIFY_EMBED_VERBOSE=1`
when debugging GPU-provider fallback or model loading.

## Step 2: cheatsheet — most common questions

| Question                                            | Command |
|-----------------------------------------------------|---------|
| **Search & ranking**                                | |
| Most-cited paper in corpus                          | `find --by paper --rank citation_count --top-k 10` |
| Most central paper (PageRank)                       | `find --by paper --rank pagerank --top-k 10` |
| Most-cited paper that talks about X                 | `find "X" --by paper --rank citation_count` |
| Paper whose **title** mentions X                    | `find "X" --by paper --field title` |
| Most-relevant chunks for X                          | `find "X" --top-k 8` |
| Literal phrase / acronym / formula                  | `find "X" --text` |
| Diverse corpus entry points (PageRank + coverage)   | `sample --max 12` |
| **Authors**                                         | |
| Most-cited author                                   | `find --by author --rank citation_count` |
| Most-prolific author                                | `find --by author --rank n_papers` |
| Highest-impact author (h-index)                     | `find --by author --rank h_index` |
| Authors writing on X                                | `find "X" --by author` |
| Author profile (h, cites, coauthors)                | `show author:<key>` |
| Papers by an author                                 | `traverse author:<key> --to sources --rank citation_count` |
| Co-authors of an author                             | `traverse author:<key> --to coauthors --rank h_index` |
| Authors of a paper                                  | `traverse doc:<short> --to authors --rank h_index` |
| **Citations**                                       | |
| Papers citing this paper                            | `traverse doc:<short> --to cited-by` |
| Most-cited papers citing this paper                 | `traverse doc:<short> --to cited-by --rank citation_count` |
| Bibliography of this paper (in-corpus targets)      | `traverse doc:<short> --to references` |
| In-corpus refs marked inside a chunk's text         | `traverse chunk:<short> --to cited-in-corpus` |
| Papers by authors who cite this paper (3-hop pipe)  | `traverse doc:X --to cited-by --format quiet \| xargs -I {} traverse {} --to authors --format quiet \| sort -u \| xargs -I {} traverse {} --to sources --format quiet \| sort -u` |
| **Structure & media**                               | |
| Chunks of a paper                                   | `traverse doc:<short> --to chunks` |
| Figures of a paper                                  | `traverse doc:<short> --to figures` |
| Figures discussed near a chunk                      | `traverse chunk:<short> --to figures` |
| Equations of a paper                                | `traverse doc:<short> --to equations` |
| One figure's metadata + on-disk path                | `show figure:<short>/<stem>` |
| Read a figure visually                              | `show figure:...`, then pass the printed `path` to the Read tool |
| One equation's LaTeX                                | `show equation:<short>` |
| **Inspection**                                      | |
| Paper metadata (title, year, authors, ...)          | `show doc:<short>` |
| Full chunk text                                     | `show chunk:<short> --full` |
| Corpus health (doc/chunk counts, derived artifacts) | `check` |
| List all docs                                       | `list docs` |
| Chunks of a specific doc                            | `list chunks --doc <doc-id>` |
| **Self-discovery**                                  | |
| Learn the full surface                              | `schema` |
| Preview what a command would do                     | append `--explain` to any `find` / `traverse` |

`--format quiet` prints handles only — pipe-safe and the default when
stdout is piped.

## Idiom: empty query + `--rank` = "rank everything by metric"

`find` with no query string and `--rank <graph-metric>` ranks the whole
population by that metric. Population must be specified explicitly: use
`--by paper` for source-typed metrics (`citation_count`, `pagerank`) or
`--by author` for author-typed metrics (`h_index`, `citation_count`,
`n_papers`). `--by chunk` (the default) is **rejected** with metric
ranks — chunks have no graph-metric to rank by; ask for papers or
authors instead.

```bash
wikify corpus find --by paper  --rank citation_count --top-k 10
wikify corpus find --by paper  --rank pagerank        --top-k 10
wikify corpus find --by author --rank h_index         --top-k 10
wikify corpus find --by author --rank citation_count  --top-k 10
```

When a query is supplied, `--rank <metric>` *re-orders* the semantic
top-K by that metric instead — useful for "most cited paper that
discusses X" where pure semantic top-1 might be a recent paper with
few citations.

## Capability surface (reference)

- **Handles**: `doc:` / `chunk:` / `figure:` / `equation:` / `author:`.
  All accept short forms (12-hex doc/chunk suffix, `<doc-short>/<stem>`
  for figures, `first_last` for authors). Any unique suffix or
  case-insensitive prefix resolves; ambiguous matches return an error
  with candidates.
- **Output formats** (`--format auto|quiet|compact|json`): `quiet`
  emits handles only and is the default when stdout is piped;
  `compact` is tab-separated columns; `json` for tooling. `auto`
  consults `WIKIFY_CLI_FORMAT` (env override), then falls back to
  `compact` for TTY / `quiet` for pipe.
- **Traverse relations**:
  - doc: `cited-by`, `references`, `chunks`, `figures`, `equations`, `authors`
  - chunk: `source`, `cited-in-corpus`, `figures`, `equations`
  - author: `sources`, `coauthors`
- **Rank metrics**:
  - sources: `citation_count`, `pagerank`
  - authors: `h_index`, `citation_count`, `n_papers`
- **Figures consume two ways**: pass the printed `path` to the Read
  tool for visual ingestion, or compose `![caption](path)` markdown
  for wiki pages.

## Default loop

1. Start with a small query, listing, or schema lookup.
2. Inspect returned handles and previews.
3. Pick one or more handles.
4. Traverse one hop or show one selected handle.
5. Narrow or broaden based on the result.
6. Pull full text only after choosing the handle.

## Does not do

- Does not mutate a bundle, add concepts, or pick an exploration strategy.
- Does not decide whether evidence is sufficient for writing.

## References

- `references/corpus-cli-patterns.md` — full grammar, format columns,
  handle rules, **environment variables**, complete worked examples.
- `references/corpus-recursive-search.md` — multi-hop pipelines.
- `references/corpus-graph-traversals.md` — relation catalogue + recipes.
- `../wikify/references/cli/grammar.md` — shared CLI grammar.
- `../wikify/references/cli/output-contract.md` — output conventions.
