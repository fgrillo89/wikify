---
name: wikify-search-corpus
description: Explain and use the Wikify corpus CLI as the read/search surface over the corpus fluent API. Use when probing corpus documents, chunks, authors, citations, figures, equations, sampling diverse entry points, semantic search, text search, paper-level ranking by citation count, or recursive graph traversals. This skill is read-only and does not decide an exploration strategy.
allowed-tools: Bash(wikify corpus *) mcp__wikify__context_show mcp__wikify__context_set mcp__wikify__corpus_find mcp__wikify__corpus_traverse mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_schema mcp__wikify__corpus_image
---

# wikify-search-corpus

Use this skill to inspect and search an existing corpus. It teaches the
available corpus read operations and traversal patterns; it does not
decide what to explore next.

## MCP mode

If `mcp__wikify__corpus_schema` is in the tool list, prefer MCP tools
over CLI verbs for repeated reads. Validation + data are identical.
High-leverage params: `corpus_show(mode="full")` for the whole body
as one string; `corpus_find(by="paper", field="title")` for literal
title substring; `corpus_traverse(chunk:..., to="cited-in-corpus")`
to follow markers; `corpus_image(figure:...)` to see the binary.
See `../wikify/references/mcp/{setup,tool-map,resources,fallback}.md`.

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
| **Unsure which mode?** Semantic + BM25 + text       | `find "X" --rank all --top-k 12` |
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

## Picking a `--rank` for chunk search

`--rank all` is the safe default when you can't predict the query shape.
It fans out to semantic + bm25 + literal substring, RRF-fuses, dedupes,
and tags each row `via=sbt` (s=semantic, b=bm25, t=text). `via=sbt`
means all three modes agreed; high-confidence consensus. Tolerates a
failure in any single mode (FTS5 syntax error, missing embedder, etc).

| Query shape                          | Recommended rank |
|--------------------------------------|------------------|
| Concept / paraphrase / question      | `semantic` (default) or `all` |
| Exact term, acronym, formula         | `bm25` (sub-ms) |
| Term + paraphrase, weighted          | `hybrid` |
| Don't know / agent exploration       | `all` |

FTS5 grammar gotchas — apply to `bm25` / `hybrid` only, not `all`:
default operator is AND, so `"what is growth per cycle"` needs every
word present (use `semantic` or `all` for natural questions); `-`
parses as NOT at query time even with `tokenchars '-'` (quote
hyphenated phrases: `'"self-limiting"'`). See
`references/corpus-cli-patterns.md` for full syntax.

## Recipe: finding the definition of a term

Definitions in primary literature don't follow Wikipedia patterns
(`"<term> is"` is rare). Try in order:

1. `find '"<term> (<ABBREV>)"' --rank bm25 --top-k 5` — the parenthesised
   intro form. Authors introduce a term with its abbreviation in one
   sentence and define it in the next.
2. `find '"<ABBREV> is"' --rank bm25 --top-k 5` — body-text definition
   using the abbreviation. Works for explained concepts (techniques,
   materials); useless for operational quantities (rates, currents,
   yields) which are introduced by value, not prose.
3. `find '"<term>"' --rank bm25 --top-k 10` — fallback. Skim previews
   for the parenthesised abbreviation; the definition lives next to it.

Skip `'"<full term> is"'` — once authors introduce the abbreviation,
they use it in prose.

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

Run `corpus schema` for the full surface (handles, traverse relations,
rank metrics, formats). Highlights: `--format auto|quiet|compact|json`
(quiet = pipe default); figure handles render as `figure:<short>/<stem>`
and the printed `path` is consumable by Read for visual ingestion.

## Default loop

Small query → inspect handles + previews → pick → traverse one hop or
show → narrow or broaden → only then pull full text.

## Does not do

Mutate a bundle, pick an exploration strategy, or decide whether
evidence is sufficient for writing.

## References

- `references/corpus-cli-patterns.md` — full grammar, format columns,
  handle rules, **environment variables**, complete worked examples.
- `references/corpus-recursive-search.md` — multi-hop pipelines.
- `references/corpus-graph-traversals.md` — relation catalogue + recipes.
- `../wikify/references/cli/grammar.md` — shared CLI grammar.
- `../wikify/references/cli/output-contract.md` — output conventions.
