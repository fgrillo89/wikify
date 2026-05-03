# Chunk Hygiene + Section Labeling + Equation Plan

Purpose: make the chunk surface that powers retrieval lossless on
content and clean of boilerplate, fix section labelling on the long
tail of docs that currently fall through, and repair the equations
pipeline. Driven by what an agent actually consumes during writing,
not by interface shape.

Owner preference: prefer longer chunks. When in doubt, merge rather
than split. We can afford one extra paragraph on either side; we
cannot afford to lose a sentence to a 9-char fragment.

## Goals / Non-goals

**Goals**
- Eliminate or merge nano-chunks that arise from over-splitting.
- Strip publisher / sidebar / page-number leakage from
  `section_path` without dropping real heading text.
- Tag boilerplate accurately enough that an agent can ignore it by
  default.
- Make equations actually queryable: index built, labels populated,
  per-doc and per-chunk traversal both work, optional substring search.
- Validate every stage with a measurable empirical move on
  `data/corpora/ald_all_marker`.

**Non-goals**
- No new embedder, no re-architecture of ingest, no Marker /
  Docling replacement.
- No semantic embedding for equations in this plan (deferred until
  retrieval shape is proven).
- No change to chunk handle grammar.
- No global re-ingest of every corpus until each stage is validated
  on a single rebuild.

## Empirical baseline (ald_all_marker, 2026-05-03)

```
chunks                       4985
  size median                916 chars
  size mean                  2655
  size p99                  14069  (max 14997)
  <100 char chunks           1615  (32% of corpus)  <-- nano-chunks
  100-499                     601  (12%)
  500-1999                    789  (16%)
  2000-7999                  1660  (33%)
  8000+                       320

is_boilerplate=true            13  (0.26%)         <-- under-tagged

section_path artifacts
  HTML <span> anchors         208 chunks
  HTML <sup>/<sub>            140
  page-number-as-header        22  ("**401**" form)
  "Articles you may" sidebar    7
  ACS "Recommended by" path  ~3   (deepest paths shown leak full URL)
  references in path          319
  acknowledgments in path     163
  paths with >=3 components  1114  (mostly legitimate nesting)
  "body" only                 336  (single-section across 30 docs)

docs                          208
  with at least one
   section_type=abstract      208  <-- abstract tagger works
  with only ONE distinct
   section_path                30  <-- 14% of corpus has no
                                       section structure detected

equations
  chunks linking eq ids       786 (15.8%)
  equations.json index          0 records  <-- index never built
  per-eq label coverage         n/a
```

The headline numbers: **32% of chunks are under 100 chars**, **the
equations index has zero records despite 786 chunks claiming
equation links**, **14% of docs have no section structure**, and
**ACS / Wiley sidebar text leaks into `section_path` for hundreds of
chunks**.

## Approach -- six stages, each shippable independently

Order chosen so each stage can be validated before committing the
next. Each stage names its blast radius.

### Stage 1: section_path normalisation (no schema change, smallest)

**What**: clean each `section_path` element at ingest before persist.

- Strip HTML: `<span ...>`, `<sup>`, `<sub>`, `<a href=...>`.
- Strip embedded URLs (`http://...`, `https://...`).
- Strip leading bullet artifacts: `- **N**` (page numbers as headers),
  `[unicode-square] **REFERENCES**` -> `REFERENCES`.
- Drop heading components that are just punctuation, just digits, or
  match a small denylist of publisher-sidebar phrases
  ("Recommended by ACS", "Articles You May Be Interested In").
- Preserve legitimate nesting; do not collapse intentional
  hierarchy.

**Where**: `src/wikify/ingest/parsers/_sections.py` --
`_clean_toc_title()` already exists; extend it and add an
equivalent for `section_spans`'s heading text. Validate against the
"deepest path" examples we already have on the corpus.

**Validation**:
- `html_sup_or_sub`, `html_span_anchor`, `articles_sidebar`,
  `section_starts_with_dash_bold_number` counts must drop to 0.
- `section_type=abstract` count must not regress (still 208).
- A handful of golden chunks from ald_all_marker (e.g.
  `chunk:e9873a2a`, `chunk:380eb7a2`) must show clean section paths
  on rebuild.

**Blast radius**: parsers + a focused test fixture file.

### Stage 2: boilerplate tagging coverage

**What**: extend `boilerplate.is_boilerplate()` and chunker hard
filter to catch the categories the audit and probe surfaced.

- Publisher article-recommendation widgets ("Articles You May Be
  Interested In", "Recommended by ACS", "Cited By"): a chunk whose
  `section_path` head element matches any of those phrases is
  boilerplate, regardless of word count.
- Page-footer chunks: text dominated by `Downloaded from
  https://onlinelibrary.wiley.com/...` plus a date stamp.
- Reference-list chunks where the chunk is a multi-entry bibliography
  (the `chunk:8e06e6c4` "32 numbered references in one chunk" case):
  use `section_type=references` AND chunk length > 4000 AND a high
  density of `\[\d+\]` markers (e.g. >10 markers / 1000 chars).
- Acknowledgments paragraphs that lead with funding language. Already
  caught by `section_type=acknowledgments`; surface that signal in
  retrieval (Stage 6 hands the agent a kind tag).

**Where**: `src/wikify/ingest/boilerplate.py` plus a new
section-path-based fast path in `chunker.py` so the soft flag fires
when the chunk inherits a sidebar parent.

**Validation**:
- `chunks_flagged_boilerplate` rises from 13 to a
  defensible number (anchor: at least the 7 articles-sidebar chunks
  + 22 page-number chunks + the chunks under publisher recommendation
  paths). Document the new floor.
- No regression on `tests/wikify/test_chunker.py` real-content
  fixtures (CC-BY abstract, body prose mid-license).
- New fixture: a small synthetic doc with each new pattern, asserting
  flagged/not-flagged.

**Blast radius**: `boilerplate.py`, `chunker.py`, new tests under
`tests/wikify/test_boilerplate*.py`.

### Stage 3: chunker -- merge tiny chunks, prefer length

**What**: change splitter post-pass so that any chunk shorter than
`MIN_CHUNK_CHARS` (currently 200) gets merged into its predecessor
within the same `section_type` bucket, unless merging would exceed
`max_chunk_chars()`. Today the chunker accepts fragments down to 30
alphanumerics (config.py:53), and the long tail of <100-char chunks
shows that floor is too low.

- Raise the soft floor to 500 chars (configurable).
- After splitting, do one merge pass: walk chunks in `ord` order;
  whenever `len(chunk[i]) < FLOOR` AND `chunk[i].section_path ==
  chunk[i-1].section_path`, concatenate text+char_span and reissue
  one chunk id; redistribute equation_ids accordingly.
- Keep figure/equation caption chunks as their own short chunks
  (they are correctly tagged by `section_type` and are typically
  what chunk-find should NOT rank against body anyway -- see Stage 6).
- Keep abstract chunks intact even if shorter than the floor.

**Where**: new pass in `chunker.py:chunk_document()` after
`_split_section()` returns.

**Validation**:
- `<100 char chunks` ratio falls from 32% to under 5% on rebuild.
- Chunk count drops modestly (target: 4985 -> 3500-4000).
- `corpus_find` against fixed query set still surfaces the same
  paragraphs it surfaced before (assertion: top-N chunk text overlap
  >= 80% by paragraph-source); we do not lose evidence.
- `equation_ids` per-chunk coverage does not regress.

**Blast radius**: `chunker.py`, `chunker` tests, regenerate test
fixtures.

### Stage 4: section detection robustness

**What**: investigate the 30 docs that have a single `["body"]`
section_path. Two suspects:

1. Marker dropped headings entirely on these docs (scanned PDFs,
   image-heavy slides, or DOCX with non-standard styles).
2. Heading parser hit them but every heading was filtered as
   "looks like a page number / boilerplate" by the existing
   stripper.

**Where**: `src/wikify/ingest/parsers/_sections.py` plus a new
diagnostic helper that emits, per doc, "n_chunks, n_distinct_paths,
top-3 paths". Run once across the corpus, decide whether this is a
parser issue (Marker) or a stripper issue (us).

**Validation**:
- Reduce `docs_with_only_one_section_path` from 30 to under 10. The
  remaining cases are documented (likely OCR-only PDFs where no
  structure is recoverable).
- Add per-doc structure score to `corpus check --full` output so
  regressions are visible.

**Blast radius**: `parsers/_sections.py`, possibly a new
`tests/wikify/parsers/` fixture per failure mode discovered.

### Stage 5: equations index repair

**What**: the per-chunk `equation_ids` are populated (786 chunks)
but `equations.json` is empty (0 records). The merge step is broken
or never runs.

- Trace where `build_equations_index()` is called from. Verify it
  runs in `ingest_corpus`. If it does, verify it reads from the
  right source.
- If it runs but writes empty: check the de-dupe key path. The
  normalized-LaTeX key may be empty for `kind=image` /
  `kind=unicode` extractions whose latex string isn't actually
  LaTeX.
- If it doesn't run: wire it up, add a coverage check in `corpus
  check --full`.

**Where**: `src/wikify/ingest/equations_index.py`,
`src/wikify/ingest/pipeline.py` (call site), and
`src/wikify/corpus/queries.py:check_corpus` (health surface).

**Validation**:
- `equations.json` non-empty after rebuild; record count plausible
  (786+ unique with dedupe across docs).
- `corpus_traverse(chunk:..., to="equations")` returns rows for at
  least one chunk per doc that has equations.
- `corpus check --full` reports equations index size.

**Blast radius**: equations index module, pipeline, health summary.

### Stage 6: agent-facing kind tag + retrieval filter

**What**: per the audit's recommendation #6, surface a chunk `kind`
tag (`body|abstract|references|acknowledgments|caption|figure|sidebar`)
on every find result row and accept an `exclude_kinds` parameter on
`corpus_find` so the agent can keep retrieval clean by default.

- The `section_type` enum already has most of these. Map sidebar
  and caption explicitly during chunking.
- Add `exclude_kinds: list[str]` to `queries.find()` and forward
  through CLI / MCP.
- Default `exclude_kinds=["references", "acknowledgments",
  "sidebar"]` is **not** baked in; the agent passes it explicitly.
  We surface the filter, not the policy.

**Where**: `src/wikify/corpus/queries.py:find` +
`_search_chunks_*`; CLI flag in `cli/corpus.py`; MCP param in
`mcp/server.py`.

**Validation**:
- `find(exclude_kinds=["references"])` returns zero chunks whose
  `section_type='references'`.
- The audit query "atomic layer deposition definition" no longer
  ranks `chunk:470eb224` (acknowledgments) when
  `exclude_kinds=["acknowledgments"]` is passed.

**Blast radius**: queries.py, cli/corpus.py, mcp/server.py, tests
across all three.

## Validation strategy (cross-stage)

- Each stage ships with a focused unit test plus an empirical assertion
  on the rebuilt `ald_all_marker` corpus (recorded as a one-line
  number in this doc's "after" table).
- Maintain a "before / after" snapshot table at the bottom of this
  file as stages land. Numbers move; the doc captures the diff.
- Add a `tests/wikify/ingest/test_chunk_hygiene.py` regression file
  that locks the new floors / artefact counts against synthetic
  fixtures so future ingest tweaks cannot silently regress.

## Rollout

- Stage 1 first (section_path) -- smallest risk, cleanest signal.
- Then Stage 5 (equations index) -- isolated repair, high value.
- Then Stage 2 (boilerplate) -- builds on Stage 1's cleaner paths.
- Then Stage 3 (merge tiny chunks) -- the biggest behaviour change;
  ship after Stages 1, 2 reduce noise so the merge sees clean inputs.
- Stage 4 (section detection) opportunistically; depends on what
  the diagnostic helper reveals.
- Stage 6 last -- a thin filter on top of everything else, valuable
  only after the underlying tags are accurate.

## Open questions

- Is there an existing chunk-rebuild path that does NOT re-parse?
  If yes, Stages 1-3 can rebuild faster than full ingest. If no,
  add a `corpus rechunk` command that reads markdown + sections from
  disk and re-runs only the chunker + boilerplate detector + equations
  binding. Saves hours on iteration.
- Should `section_type=caption` be split out from `body`? Captions
  rank weirdly (the audit's `chunk:98c135a9ce26/Figure_02__caption`
  case) and a separate kind makes filtering trivial.
- For Stage 3's merge pass, should we cross section-path boundaries
  when the parent is the same and the leaf is a tiny stub?
  Conservative answer: no; the section_path carries information that
  the agent uses for trust scoring. Merge only within identical
  section_path.

## Empirical "after" tracking

| Metric | Baseline | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Stage 5 |
|---|---|---|---|---|---|---|
| <100 char chunks | 1615 (32%) | - | - | target <250 (5%) | - | - |
| HTML span anchors in path | 208 | target 0 | - | - | - | - |
| Articles-sidebar paths | 7 | target 0 | - | - | - | - |
| Page-number-as-header | 22 | target 0 | - | - | - | - |
| Boilerplate flagged | 13 | - | target >=80 | - | - | - |
| Single-`body` docs | 30 | - | - | - | target <10 | - |
| equations.json records | 0 | - | - | - | - | target 786+ |

Numbers update inline as each stage lands.
