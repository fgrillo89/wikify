# Corpus CLI Patterns

The corpus is the authoritative evidence layer. During a wiki run,
corpus access is read-only unless a workflow explicitly runs ingest or
refresh outside the bundle.

## Step 0 — discover the surface

```bash
wikify corpus schema
```

Lists node types, edge kinds, traverse relations grouped by handle
kind, and rank metrics. Refer here, not source code, for "what's
available."

Append `--explain` to any `find` or `traverse` to print the resolved
fluent-chain pseudocode and exit without executing.

## Step 1 — set the corpus once and pick a default format

`--corpus` is **optional**. Resolution order:

1. Explicit `--corpus <path>` flag.
2. `WIKIFY_CORPUS` environment variable.
3. Walk up from cwd looking for a directory with `manifest.json` and `docs/`.

```bash
export WIKIFY_CORPUS=data/corpora/<name>
export WIKIFY_CLI_FORMAT=compact   # rich rows for non-TTY agents
```

`--format auto` (the default) resolves through these steps:

1. `WIKIFY_CLI_FORMAT` if it's `compact` / `quiet` / `json`.
2. `compact` when stdout is a TTY.
3. `quiet` (handle-only, pipe-safe) otherwise.

Without the env var, agent shells (which are not TTYs) get `quiet` and
never see scores, citation counts, or titles. Set it once at session
start.

## Idiom — empty query + `--rank` = "rank everything by metric"

`find` with no query and `--rank <graph-metric>` ranks the whole
population by that metric. Population must be specified explicitly:
`--by paper` for source-typed metrics (`citation_count`, `pagerank`)
or `--by author` for author-typed metrics (`h_index`,
`citation_count`, `n_papers`). `--by chunk` (default) is rejected with
a graph-metric rank — chunks have no metric to rank by; the CLI tells
you to switch to `--by paper` or `--by author`.

With a query, `--rank` re-orders the semantic top-K by the metric
instead of ranking the whole population.

```bash
wikify corpus find --by paper  --rank citation_count --top-k 10   # most-cited paper
wikify corpus find --by author --rank h_index        --top-k 10   # highest h-index
wikify corpus find "X" --by paper --rank citation_count           # most-cited about X
```

## Common Commands

```bash
wikify corpus schema [--format text|json]      # self-describe the surface
wikify corpus check [<corpus>] [--format text|json]
wikify corpus list docs   [--corpus <c>]
wikify corpus list chunks [--corpus <c>] [--doc <doc-id>]
wikify corpus list files  [--corpus <c>]
wikify corpus find "<query>" [--corpus <c>] [--top-k N] \
    [--by chunk|paper|author] \
    [--rank semantic|citation_count|pagerank|h_index|n_papers] \
    [--format auto|quiet|compact|json] [--explain]
wikify corpus find "<query>" [--corpus <c>] --text
wikify corpus sample [--corpus <c>] [--max N] [--strategy diverse] [--pagerank-weight W]
wikify corpus show <handle> [--corpus <c>] [--full]
wikify corpus traverse <handle> --to <relation> [--corpus <c>] \
    [--rank citation_count|pagerank|h_index|n_papers] [--top-k N] \
    [--format auto|quiet|compact|json] [--explain]
wikify corpus repl [--corpus <c>]
```

Missing-corpus-everywhere produces a clear error listing all three
resolution options.

## Handles

Five handle kinds: ``doc:``, ``chunk:``, ``figure:``, ``equation:``,
``author:``. The CLI accepts and emits **short handles**:

- ``doc:<12-hex>`` / ``chunk:<8-hex>`` / ``equation:<12-hex>`` —
  trailing hex suffix.
- ``figure:<doc-short>/<stem>`` — e.g. ``figure:514791d621fa/fig_002``.
- ``author:first_last`` — lowercase author key with spaces replaced by
  underscores. Case-insensitive unique prefix is also accepted.

Full ids work everywhere. Any unique suffix resolves; ambiguous matches
return an error listing the candidates.

## Output Formats

- ``quiet``    one short handle per line; nothing else. Pipe-safe.
- ``compact``  tab-separated columns. Default for TTY; recommended
                via ``WIKIFY_CLI_FORMAT=compact`` for agent shells.
- ``json``     existing JSON shape, for tooling.
- ``auto``     ``WIKIFY_CLI_FORMAT`` if set, else ``compact`` for TTY,
                else ``quiet``.

Unknown ``--format`` values now produce a structured ``bad_format``
error envelope, not a Python traceback. For ``find`` and ``sample``,
``--top-k`` / ``--max`` must be ``> 0`` (``0`` and negative values are
rejected). For ``traverse``, ``--top-k 0`` is allowed and means
*unlimited*; only negative values are rejected.

### Compact Column Meanings

| Command / mode | Columns |
|---|---|
| ``find --by chunk`` (default)   | ``score`` ``cites=N`` ``chunk-handle`` ``doc-handle`` |
| ``find --by paper``             | ``score`` ``cites=N`` ``n=K`` ``doc-handle`` ``title`` |
| ``sample`` / metric-only ranking | ``cites=N`` ``pr=X.XXXX`` ``doc-handle`` ``title`` |
| ``traverse`` source result      | ``cites=N`` ``pr=X.XXXX`` ``doc-handle`` ``title`` |
| ``traverse`` chunk result       | ``chunk-handle`` ``doc-handle`` |
| ``traverse`` figure result      | ``page=N`` ``figure-handle`` ``caption`` ``path`` |
| ``traverse`` equation result    | ``kind`` ``label`` ``equation-handle`` ``latex`` |
| ``traverse`` author result      | ``h=N`` ``cites=N`` ``n_papers=N`` ``author-handle`` ``name`` |
| ``find --by author "<query>"``  | ``score`` ``h=N`` ``cites=N`` ``n_match=N`` ``author-handle`` ``name`` |
| ``find --by author --rank …`` (no query) | ``h=N`` ``cites=N`` ``n_papers=N`` ``author-handle`` ``name`` |

Where:

- ``score`` — semantic cosine similarity, range ~0..1, higher is closer.
  Printed as ``.`` when ``--text`` skips embedding.
- ``cites=N`` — **in-corpus inbound** citation count: how many papers
  inside this corpus cite the doc. Not the doc's own bibliography size,
  not a global citation count. The number depends on corpus scope:
  a narrow specialised corpus produces large ``cites`` for foundational
  papers; a broad corpus produces small ``cites`` for the same paper.
- ``pr=X.XXXX`` — PageRank over the corpus citation graph.
- ``n=K`` (paper rows only) — how many chunks of that paper matched
  the query. Higher ``n`` means the topic spans more of the paper.
  Bounded by the internal chunk pool used for aggregation.

## Aggregation And Ranking

- ``--by chunk`` (default) returns ranked chunks. ``--by paper``
  aggregates each chunk to its paper, returning best-chunk-per-paper.
- ``--rank semantic`` (default) keeps the embedding score order.
  ``--rank citation_count`` and ``--rank pagerank`` re-rank papers by
  the corresponding graph metric. With ``--rank citation_count`` and no
  query, ``find`` returns the most-cited papers in the corpus.

## Traverse Relations

For a ``doc:`` handle: ``cited-by``, ``references``, ``chunks``,
``figures``, ``equations``, ``authors``.

For a ``chunk:`` handle: ``source``, ``cited-in-corpus``, ``figures``
(figures discussed near this chunk via FIGURE_NEAR_CHUNK), ``equations``
(equations contained in this chunk).

For an ``author:`` handle: ``sources`` (papers by this author),
``coauthors`` (authors who share a paper with this one). Both accept
``--rank h_index | citation_count | n_papers``.

## Figures And Equations

Figure handles are ``figure:<doc-short>/<stem>`` (e.g.
``figure:514791d621fa/fig_002``). Equation handles are bare hex
(``equation:d4dbfe68ba7d``).

``corpus show figure:<short>`` prints the corpus-relative ``path``,
``caption``, ``page``, and the chunk handles where the figure is
discussed. The path is what enables both seamless agent paths:

- **Visual ingestion**: pass the path directly to the Read tool —
  Claude Code's Read tool decodes PNG/JPG and presents the figure
  visually. No extra step.
- **Markdown linking**: compose ``![<caption>](<path>)`` for inclusion
  in a wiki page.

``corpus show equation:<short>`` prints the LaTeX, ``kind``
(``math`` / ``chem`` / ``named``), and ``label``.

## Interactive Session

Use `wikify corpus repl --corpus <corpus>` when a workflow needs many
iterative corpus queries. The process keeps docs/chunks indexed and
loads the semantic embedder only once after the first semantic `find`.

```text
find atomic layer deposition HfO2 memristor top=10
find-papers atomic layer deposition HfO2 memristor top=10
find --text "atomic layer deposition" top=20
show chunk:<chunk-id> full
list docs
sample max=20
exit
```

`find` returns chunks. `find-papers` groups the best matching chunks by
paper and returns `best_score`, match count, `doc_id`, and
`best_chunk_id`. Use it when the workflow needs the most relevant full
paper for a concept before drilling into chunks.

## Query Shapes

- Concept query: subject name, alias, method, material, device, person.
- Exact phrase query: acronym, equation label, material formula,
  section heading, quoted term.
- Sampling: `corpus sample` to expose central, mutually-distinct corpus entry points without a query.
- Evidence query: concept title plus a missing aspect, for example
  `"atomic layer deposition temperature window"`.
- Disambiguation query: title plus field or source context.

## Full Text Discipline

Do not open full documents or chunks by default. Use previews to choose
a handle first. Then call `show --full` on the specific selected handle.

## Environment Variables

| Env var                | Effect                                                              |
|------------------------|---------------------------------------------------------------------|
| ``WIKIFY_CORPUS``        | Default corpus path (skip ``--corpus``).                              |
| ``WIKIFY_CLI_FORMAT``    | ``compact`` / ``quiet`` / ``json`` — overrides ``--format auto`` for non-TTY callers (every agent shell). |
| ``WIKIFY_EMBED_VERBOSE`` | ``1`` to re-enable the embedder model + health-check banners on stderr. Default off. |
| ``WIKIFY_QUIET``         | ``1`` to suppress informational hints (e.g. the "0 markers resolved" hint from ``traverse <chunk> --to cited-in-corpus``). |

## Worked Examples

```bash
# Discover the surface.
wikify corpus schema

# Search.
wikify corpus find "atomic layer deposition" --top-k 8
wikify corpus find "atomic layer deposition" --by paper --rank citation_count --top-k 3
wikify corpus find "HfO2" --text
wikify corpus sample --max 12

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
