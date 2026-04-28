---
name: wikify-search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, seed documents, semantic search, text search, paper-level ranking by citation count, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
allowed-tools: Bash(wikify corpus *)
---

# wikify-search-corpus

Use this skill to inspect and search an existing corpus. It teaches the
available corpus read operations and traversal patterns; it does not
decide what to explore next.

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

## Step 1: resolve the corpus once

`--corpus` is optional. Resolution order:

1. Explicit `--corpus <path>` flag.
2. `WIKIFY_CORPUS` environment variable.
3. Walk up from cwd looking for a directory with `manifest.json` and `docs/`.

The cleanest pattern:

```bash
export WIKIFY_CORPUS=data/corpora/<my-corpus>
```

Examples below omit `--corpus` because `WIKIFY_CORPUS` is set.

## Step 2: cheatsheet — most common questions

| Question                                            | Command |
|-----------------------------------------------------|---------|
| **Search & ranking**                                | |
| Most-cited paper in corpus                          | `find --rank citation_count --top-k 10` |
| Most central paper (PageRank)                       | `find --rank pagerank --top-k 10` |
| Most-cited paper that talks about X                 | `find "X" --by paper --rank citation_count` |
| Most-relevant chunks for X                          | `find "X" --top-k 8` |
| Literal phrase / acronym / formula                  | `find "X" --text` |
| Diverse corpus entry points (PageRank + coverage)   | `find --seed --max 12` |
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
| Papers by authors who cite this paper (3-hop pipe)  | `traverse doc:X --to cited-by -F quiet \| xargs -I {} traverse {} --to authors -F quiet \| sort -u \| xargs -I {} traverse {} --to sources -F quiet \| sort -u` |
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

(`-F` is the short form of `--format`. `quiet` mode prints handles
only — pipe-safe.)

## Idiom: empty query + `--rank` = "rank everything by metric"

`find` with no query string and `--rank <graph-metric>` returns the
corpus's top-K by that metric. Works for `--by chunk` (default — the
top docs are emitted), `--by paper`, and `--by author`. This is how
"who's most cited in this corpus?" becomes one line:

```bash
wikify corpus find --by author --rank citation_count --top-k 5
wikify corpus find --rank pagerank --top-k 10
```

When a query is supplied, `--rank <metric>` *re-ranks* the semantic
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
  `compact` is tab-separated columns for TTY; `json` for tooling.
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

## Examples (omitting `--corpus`; `WIKIFY_CORPUS` is set)

```bash
# Discover the surface.
wikify corpus schema

# Search.
wikify corpus find "atomic layer deposition" --top-k 8
wikify corpus find "atomic layer deposition" --by paper --rank citation_count --top-k 3
wikify corpus find "HfO2" --text
wikify corpus find --seed --max 12

# Authors.
wikify corpus find --by author --rank h_index --top-k 5
wikify corpus find "memristor" --by author --top-k 5
wikify corpus show author:sungjun_kim
wikify corpus traverse author:sungjun_kim --to sources --rank citation_count
wikify corpus traverse author:sungjun_kim --to coauthors --rank h_index

# Drill into one paper.
wikify corpus show doc:<short>
wikify corpus show doc:<short> --full
wikify corpus traverse doc:<short> --to authors --rank h_index
wikify corpus traverse doc:<short> --to cited-by --rank citation_count --top-k 5
wikify corpus traverse doc:<short> --to figures
wikify corpus traverse doc:<short> --to equations --top-k 5

# Drill into one chunk.
wikify corpus show chunk:<short> --full
wikify corpus traverse chunk:<short> --to cited-in-corpus --rank citation_count
wikify corpus traverse chunk:<short> --to figures

# Media.
wikify corpus show figure:<short>/<stem>
wikify corpus show equation:<short>

# Interactive session.
wikify corpus repl
```

## Does not do

- Does not mutate a bundle.
- Does not add concepts or evidence.
- Does not choose an exploration strategy.
- Does not decide whether evidence is sufficient for writing.

## References

- `references/corpus-cli-patterns.md` — full grammar, format columns, handle rules.
- `references/corpus-recursive-search.md` — multi-hop pipelines.
- `references/corpus-graph-traversals.md` — relation catalogue + recipes.
- `../wikify/references/cli/grammar.md` — shared CLI grammar.
- `../wikify/references/cli/output-contract.md` — output conventions.
