# CLI: sampling and fetch

Spec for three related improvements to the corpus CLI. Each section is
independently implementable; together they generalise the original
`corpus find --seed` into a small, composable sampling/fetch surface.

Status: partial. Section 1 has shipped in skeleton form as the
top-level verb `wikify corpus sample --strategy diverse --max N
--pagerank-weight W` (PR #58, replacing the old `find --seed` entry
point). The richer strategy/population/filter matrix below
(`top|random|weighted|periphery|stratified`, populations beyond
`docs`, filters) is still spec. Sections 2 and 3 are unchanged: design
only, not yet implemented.

## 1. Generalise `--seed` into a `sample` verb

### Problem

`corpus find --seed --max 12 --pagerank-weight 0.7` bundles three
distinct decisions:

1. **What population are we drawing from?** (docs, chunks, authors.)
2. **How are we weighting the draw?** (pagerank, citation_count,
   h_index, uniform, ...)
3. **What diversity constraint, if any?** (greedy submodular for
   coverage.)

Bundling locks them together. A workflow that wants "12 random papers
weighted by pagerank, no diversity penalty" or "8 chunks discussing X
sampled to avoid top-K bias" cannot express it.

`--seed` was named for the original baseline strategy
(seed-then-extract-concepts). Sampling is the more general primitive,
and several other strategies want it independently of the seed-extract
flow.

### Goals

- One verb (`sample`) that exposes population, strategy, and filters
  as orthogonal flags.
- Strategies for both quality (top-by-metric, submodular) and
  exploration (random, weighted, periphery, stratified).
- Reproducibility via `--rng-seed`.
- Backwards-compatible: `find --seed` keeps working as a deprecated
  alias for one specific composition.
- Composes with the existing handle / `--format quiet` round trip so
  pipelines like `sample ... | xargs traverse ...` work.

### Non-goals

- Multi-objective scoring (e.g. fused score = ?*pagerank +
  (1-?)*coverage). The current `--seed` does this internally for
  submodular+pagerank; we keep that as one named strategy but do not
  generalise to arbitrary linear combos.
- New metrics. Use whatever the graph already exposes
  (`pagerank`, `citation_count`, `h_index`, `n_papers`, `year`,
  `n_chunks`, `n_figures`).

### CLI grammar

```
wikify corpus sample <population> --strategy <name> [strategy flags] \
    [--filter ...] [--top-k N] [--rng-seed N] \
    [--format auto|quiet|compact|json] [--explain]
```

Populations:

- `docs`     — sources
- `chunks`   — chunks (any), with optional pre-filter
- `authors`  — authors

Strategies (per population):

| Strategy | Populations | Required flags | Notes |
|---|---|---|---|
| `top`         | docs, chunks, authors | `--by <metric>` | Deterministic top-K by metric. Equivalent to current `find --rank` with no query. |
| `random`      | docs, chunks, authors | -                | Uniform draw. `--rng-seed` for reproducibility. |
| `weighted`    | docs, authors         | `--by <metric>`  | Probability ? metric value. Zero-weight rows are excluded. |
| `submodular`  | docs                  | `--pagerank-weight W` | Greedy submodular coverage with PageRank prior. The current `--seed` behaviour. |
| `periphery`   | docs, authors         | `--by <metric>`  | Bottom-K (long tail). Useful for surfacing under-cited or niche material. |
| `stratified`  | docs, chunks          | `--by <attr>`    | K per stratum. e.g. `--by year`, `--by section_type`, `--by source_id` (k chunks per paper). |

Filters (applied before sampling, narrow the population):

- `--match field=value` — exact attr match (e.g. `section_type=methods`).
- `--since YEAR` — year >= N.
- `--where k=v` — generic attr filter (multiple allowed).
- For chunks only:
  - `--text "<phrase>"` — literal substring grep.
  - `--semantic "<query>" [--threshold T]` — chunks whose cosine to
    the query embedding is >= T (default 0.4). Combine with
    `--strategy random` to get a representative sample of chunks that
    discuss the topic, instead of top-K which over-reps the model's
    specific phrasing.

### Examples

```bash
# Original `--seed` behaviour, explicit:
wikify corpus sample docs --strategy submodular \
    --pagerank-weight 0.7 --top-k 12

# Top 5 papers by citation count (was: find --rank citation_count):
wikify corpus sample docs --strategy top --by citation_count --top-k 5

# 8 random papers, reproducible, weighted by pagerank:
wikify corpus sample docs --strategy weighted --by pagerank \
    --top-k 8 --rng-seed 42

# Periphery: 10 lowest-cited authors (long tail of the field):
wikify corpus sample authors --strategy periphery \
    --by citation_count --top-k 10

# Stratified: 2 chunks per paper across the corpus, only methods sections:
wikify corpus sample chunks --strategy stratified \
    --by source_id --match section_type=methods --top-k 2

# Random chunks discussing ALD, threshold-filtered (avoid top-K bias):
wikify corpus sample chunks --strategy random \
    --semantic "atomic layer deposition" --threshold 0.5 \
    --top-k 12 --rng-seed 7

# Stratified by year: 2 papers per year since 2018, weighted by pagerank
# within each stratum:
wikify corpus sample docs --strategy stratified --by year \
    --since 2018 --weighted-by pagerank --top-k 2
```

### Migration from `find --seed`

What actually shipped (PR #58) was the simpler, breaking variant:

- `find --seed` was removed outright; no deprecated alias. The fluent
  helper `corpus.queries.find_seeds` was renamed to
  `corpus.queries.sample_docs`, and the strategy implementation moved
  to a new `corpus/sampling.py` module.
- The CLI surface is `wikify corpus sample [--max N] [--strategy
  diverse] [--pagerank-weight W]`. Strategy names are flat
  (`diverse`, future `random`/`pagerank`/`stratified`); there is no
  `--strategy submodular --population docs` decomposition yet.
- Skill docs in `.claude/skills/wikify-search-corpus/` were migrated
  to `corpus sample` in the same PR.

The richer matrix below remains spec.

### Open questions

- **`weighted` vs `top`**: should `top` be a special case of
  `weighted` with delta-weight (all mass on the top node)? Probably
  not - keep `top` as deterministic and `weighted` as stochastic so
  the agent can pick the right semantic by name.
- **Stratified + weighted combo**: `--strategy stratified --by year
  --weighted-by pagerank` is a useful combo but adds a flag. Either
  ship it or document `for y in years: sample within --since/--until`
  pipeline as the workaround.
- **Periphery for chunks**: low cosine to corpus centroid is
  meaningful but expensive. Skip in v1; revisit if a strategy needs
  it.
- **Chunk semantic threshold**: 0.4 is a guess. Run a small
  calibration on `ald_all_marker` to pick a sensible default before
  shipping.

---

## 2. Section-scoped traversal and a `section:` handle

### Problem

`traverse doc:<short> --to chunks` returns every chunk of a paper.
Workflows that want "the methods section" or "the conclusions"
currently have to read all chunks and filter by `section_type` in
their head. The fluent API already has `sections()` and
`sections(type=...)` plus `chunks()` scoped to a section set, so this
is purely a CLI-exposure gap.

### CLI grammar

New handle kind: `section:<doc-short>/<section-id>`.

New traverse relations:

- doc -> `sections` (optionally `--type abstract|methods|results|...`)
- section -> `chunks`
- chunk -> `section` (the parent section)

`show section:<short>` prints the section's title path, type, and
chunk count.

### Examples

```bash
# All sections of a paper.
wikify corpus traverse doc:bdead88c7417 --to sections

# Only methods sections.
wikify corpus traverse doc:bdead88c7417 --to sections --type methods

# Methods chunks, in one pipe:
wikify corpus traverse doc:bdead88c7417 --to sections --type methods \
    --format quiet \
  | xargs -I {} wikify corpus traverse {} --to chunks

# What section is this chunk in?
wikify corpus traverse chunk:ade5e4d2 --to section
```

### Implementation sketch

- Extend `corpus.queries.traverse_doc` with a `sections` relation
  (already mapped to `qb.sections(type=...)`).
- Add `traverse_section(corpus, *, section_id, relation)` for
  `chunks`. Internal `_resolve_section_id` reuses the generic
  `handles.resolve` against section ids.
- Section handles use the same compound shape as figures:
  `<doc-id>/<section-key>`, shortened via `short_id` (already
  recursive on `/`).
- `cmd_show` grows a `section:` branch printing
  `id`, `doc`, `section_type`, `path`, `n_chunks`.

### Skill / doc updates

- `corpus schema` adds the section node and its relations.
- `corpus-cli-patterns.md` adds the recipe.
- The compact column for sections:
  `type \t section-handle \t path \t n_chunks`.

### Out of scope

- Section-level vector search. Sections aren't currently embedded.
- Section-level rank metrics. There's nothing meaningful to rank by.

---

## 3. Per-handle stats in `show doc:`

### Problem

`show doc:<short>` currently prints `id title kind chunks year
authors`. The agent calls it to triage a paper but then has to make
follow-up calls to learn how cited / central / well-illustrated it
is. Three follow-ups for one decision.

### Proposal

Add the graph metrics already on the node to the default output:

```
id:         [2020 Yang]_bdead88c7417
title:      Memristive Device Characteristics Engineering ...
kind:       pdf
year:       2020
authors:    9
chunks:     14
sections:   6
figures:    8
equations:  2
cites:      41          # in-corpus inbound citations
references: 58          # outbound bibliography (in-corpus targets only)
pagerank:   0.0125
h_index_first_author: 4
```

Pull from the graph node's own attrs (`citation_count`, `pagerank`)
plus cheap counts from the existing inverted indexes
(`_chunks_of_source`, `_sections_of`, `_figures_of`, `_equations_of`,
`_references`). One pass over the same KG instance the resolver
already loaded - no extra graph load.

### `--full` semantics

`show doc:<short> --full` should additionally print the abstract
chunk text (via `kg.source(id).abstract_chunk()`) plus the section
list with chunk counts. That makes `show --full` a one-call paper
overview, not a metadata dump.

### JSON shape

JSON output gains the same fields. Existing keys are unchanged so
downstream parsers don't break.

### Implementation cost

`corpus.queries.get_doc` returns a dataclass; either widen it or add
a parallel `get_doc_summary(corpus, doc_id) -> dict` that bundles the
metrics. The CLI `cmd_show` doc branch then prints the new fields.
Tests: extend `test_cli_corpus.py::test_corpus_show_doc` with the new
fields. Probably ~80 lines including tests.

### Out of scope

- Author-level equivalent (`show author:`). Already prints h_index,
  citation_count, n_papers, top coauthors - covered.
- Chunk-level equivalent (`show chunk:`). Chunks are the leaf
  evidence; no obvious extra signal worth printing by default.

---

## Order of work

Suggested sequence when these are picked up:

1. **`show doc:` stats** (smallest, isolated). Lands first; gives
   immediate ergonomic win.
2. **Section traversal**. Needed before #3 below can stratify by
   section_type cleanly.
3. **`sample` verb**. Largest piece. Lands last so it can use the
   section affordance for stratified-by-section sampling.

Each step is self-contained and shippable as its own PR.
