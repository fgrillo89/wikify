# Corpus CLI Patterns

The corpus is the authoritative evidence layer. During a wiki run,
corpus access is read-only unless a workflow explicitly runs ingest or
refresh outside the bundle.

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
wikify corpus find --seed [--corpus <c>] [--max N] [--pagerank-weight W]
wikify corpus show <handle> [--corpus <c>] [--full]
wikify corpus traverse <handle> --to <relation> [--corpus <c>] \
    [--rank citation_count|pagerank|h_index|n_papers] [--top-k N] \
    [--format auto|quiet|compact|json] [--explain]
wikify corpus repl [--corpus <c>]
```

``--corpus`` is **optional**. Resolution order:

1. Explicit ``--corpus <path>`` flag.
2. ``WIKIFY_CORPUS`` environment variable.
3. Walk up from cwd looking for a directory with ``manifest.json`` and ``docs/``.

Missing-everywhere produces a clear error listing all three options.

Run ``wikify corpus schema`` once per session to learn the full grammar
without grepping source. Add ``--explain`` to any ``find`` or
``traverse`` call to print the resolved fluent-chain pseudocode without
executing.

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
- ``compact``  tab-separated columns (default when stdout is a TTY).
- ``json``     existing JSON shape, for tooling.
- ``auto``     compact if stdout is a TTY, quiet if piped.

### Compact Column Meanings

| Command / mode | Columns |
|---|---|
| ``find --by chunk`` (default)   | ``score`` ``cites=N`` ``chunk-handle`` ``doc-handle`` |
| ``find --by paper``             | ``score`` ``cites=N`` ``n=K`` ``doc-handle`` ``title`` |
| ``find --seed`` / metric-only ranking | ``cites=N`` ``pr=X.XXXX`` ``doc-handle`` ``title`` |
| ``traverse`` source result      | ``cites=N`` ``pr=X.XXXX`` ``doc-handle`` ``title`` |
| ``traverse`` chunk result       | ``chunk-handle`` ``doc-handle`` |
| ``traverse`` figure result      | ``page=N`` ``figure-handle`` ``caption`` ``path`` |
| ``traverse`` equation result    | ``kind`` ``label`` ``equation-handle`` ``latex`` |
| ``traverse`` author result      | ``h=N`` ``cites=N`` ``n_papers=N`` ``author-handle`` ``name`` |
| ``find --by author``            | ``score`` ``h=N`` ``cites=N`` ``n_papers=N`` ``author-handle`` ``name`` |

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
seed max=20
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
- Seed query: `find --seed` to expose central corpus entry points.
- Evidence query: concept title plus a missing aspect, for example
  `"atomic layer deposition temperature window"`.
- Disambiguation query: title plus field or source context.

## Full Text Discipline

Do not open full documents or chunks by default. Use previews to choose
a handle first. Then call `show --full` on the specific selected handle.
