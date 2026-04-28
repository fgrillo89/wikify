# Corpus Graph Traversals

`wikify corpus traverse` exposes one-hop graph traversals over corpus
handles. Output is handles — pipe directly into `corpus show` or
another `corpus traverse`.

## Supported Relations

For a `doc:` handle:

- `cited-by`     sources that cite this source
- `references`   sources cited by this source
- `chunks`       chunks belonging to this source

For a `chunk:` handle:

- `source`            the doc this chunk belongs to
- `cited-in-corpus`   in-corpus sources cited by markers in the chunk's text
- `figures`           figures discussed near this chunk via FIGURE_NEAR_CHUNK
- `equations`         equations contained in this chunk

Doc relations also include `figures` and `equations` for paper-wide
listings.

## Ranking And Limits

- `--rank citation_count` or `--rank pagerank` re-orders source-typed
  results by graph metric.
- `--top-k N` truncates after rank.
- `--format quiet` (auto when piping) emits one short handle per line.

## Example Recipes

### Most Cited Paper About A Concept

```bash
wikify corpus find "atomic layer deposition" \
    --by paper --rank citation_count --top-k 3 --corpus <c>
```

### Citation Neighborhood

```bash
# Find a paper, then list who cites it, ranked by citations.
wikify corpus find "atomic layer deposition" --by paper --top-k 1 \
    --format quiet --corpus <c> \
  | xargs -I {} wikify corpus traverse {} --to cited-by \
        --rank citation_count --top-k 5 --corpus <c>
```

### In-Text Citations Resolved To Corpus

```bash
# Find a chunk, then resolve its [N] markers to in-corpus sources.
wikify corpus find "atomic layer deposition" --top-k 1 \
    --format quiet --corpus <c> \
  | xargs -I {} wikify corpus traverse {} --to cited-in-corpus \
        --rank citation_count --corpus <c>
```

`cited-in-corpus` returns empty when a chunk's markers all reference
papers outside the corpus — that is correct behavior, not a bug.

### Two-Hop: Paper -> Its References -> Citers Of A Reference

```bash
wikify corpus traverse doc:<short> --to references --top-k 1 \
    --format quiet --corpus <c> \
  | xargs -I {} wikify corpus traverse {} --to cited-by \
        --rank citation_count --top-k 5 --corpus <c>
```

### Figures: Consume Visually Or Link

The compact format puts the corpus-relative path in the last column,
ready for the Read tool:

```bash
# List figures of a paper.
wikify corpus traverse doc:<short> --to figures \
    --format compact --corpus <c>
# page=3  figure:5f92.../fig_002  Schematic of...  images/.../fig_002.png

# Inspect one figure's metadata + path.
wikify corpus show figure:5f92.../fig_002 --corpus <c>
```

Two seamless paths from there:

- **Visual ingestion**: pass the path to the Read tool — it decodes
  PNG/JPG and the model sees the figure directly. No further CLI step.
- **Markdown linking**: assemble ``![<caption>](<path>)`` for inclusion
  in a wiki page from the path + caption columns.

For figures discussed near a particular chunk (FIGURE_NEAR_CHUNK):

```bash
wikify corpus traverse chunk:<short> --to figures \
    --format compact --corpus <c>
```

### Equations

```bash
wikify corpus traverse doc:<short> --to equations \
    --top-k 5 --format compact --corpus <c>
# math   E_1   equation:d4dbfe68ba7d   I = G(V) ...

wikify corpus show equation:d4dbfe68ba7d --corpus <c>
```

Use ``--to equations`` on a ``chunk:`` handle to scope to equations
contained in that chunk only.

### Authors

```bash
# Top authors in this corpus by h-index.
wikify corpus find --by author --rank h_index --top-k 5

# Inspect one author (papers count + top coauthors).
wikify corpus show author:sungjun_kim

# All papers by an author.
wikify corpus traverse author:sungjun_kim --to sources --rank citation_count

# Co-authors of an author, ranked by their h-index.
wikify corpus traverse author:sungjun_kim --to coauthors --rank h_index

# Authors of a specific paper.
wikify corpus traverse doc:<short> --to authors --rank h_index

# Authors most associated with a topic.
wikify corpus find "atomic layer deposition" --by author --top-k 5
```

### Two-Hop: Papers By Authors Who Cite This Paper

```bash
# doc -> citers -> their authors -> all their papers
wikify corpus traverse doc:<short> --to cited-by --format quiet \
  | xargs -I {} wikify corpus traverse {} --to authors --format quiet \
  | sort -u \
  | xargs -I {} wikify corpus traverse {} --to sources --format quiet \
  | sort -u
```

Each hop is one CLI call; pipes compose them. No special multi-hop
syntax — the handle round-trip invariant carries the chain.

### Out Of Scope (Tier 2)

The following relations are intentionally not in v1 of `traverse`:
authors, coauthors, sections, figures, equations, nearby-figures,
nearby-equations, neighborhood with hops. The fluent API supports them;
they will surface in `traverse` when a workflow needs them. Until then,
fall back to `corpus repl` for ad-hoc exploration.

These are search patterns, not strategies. Workflow skills decide which
pattern to use and when to stop.
