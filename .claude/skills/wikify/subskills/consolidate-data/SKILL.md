---
name: consolidate-data
description: Consolidator that turns the bundle claim store into evolving data-artifact tables. Picks a dense subject-by-property theme, writes a durable view spec, and commits a kind=data wiki page that renders as an HTML table with per-cell references and re-derives from the spec as more data lands.
allowed-tools: Bash(wikify *)
---

# consolidate-data

Turn scattered data points into a wiki-resident comparison table. A data
artifact is a **materialized view** over the claim store, described by a
durable spec. It is never hand-edited: re-running consolidation after new
claims (or new papers) arrive refreshes the table. That is what makes the
artifact evolve. The spec is the durable thing; the table is always
re-derived.

## When to fire

Consolidate a theme once the claim store has enough to fill a table:

```bash
wikify data coverage --run <bundle> --format json
```

A theme is ripe when several **subjects** share a small set of
**properties** (e.g. 4+ materials each with a "growth per cycle" and a
"deposition temperature"). Read the candidate rows first:

```bash
wikify data list --run <bundle> --property "growth per cycle" --status verified
```

If a property name is fragmented (e.g. "GPC" vs "growth per cycle"), that is
a routing problem in the claim store. Consolidate the dominant spelling and
escalate the alias merge to the editor rather than inventing a mapping here.

## Recall gate — do not commit a sparse table

Before committing a table for a property, pull its whole-corpus recall report:

```bash
wikify data harvest-property --property "growth per cycle" \
  --alias GPC --unit "A/cycle" --corpus <corpus> --run <bundle> --format json
```

`report.data_recall = docs_in_table / docs_mentioning_property`. The table is
too thin whenever many papers report the property but few reach the table:

- Committing with `--require-recall` (below) **enforces** this gate in the CLI:
  it refuses the commit when `docs_mentioning_property >= 10` AND
  `data_recall < 0.75`, and also when a spec property has no `harvest-property`
  sweep on record. When you hit a block, loop back to `extract-data` in
  property-targeted mode to harvest the missing docs, then re-check. Bypass a
  single commit with `--skip-recall` only when you have justified the sparse
  table (the bypass is logged).
- Without `--require-recall` the gate is advisory: the CLI does not read the
  sweep, so honor the same threshold yourself before committing.
- Aim to consolidate at `data_recall >= 0.90` (or when the sweep is exhausted
  and no longer `truncated`).

This is what keeps a comparison table representative instead of shipping the
handful of rows that happened to fall in the rounds' doc slices.

## The view spec

```json
{"artifact_id": "ald-growth-per-cycle",
 "title": "ALD Growth Per Cycle by Material",
 "description": "Reported growth per cycle across ALD chemistries.",
 "properties": ["growth per cycle", "deposition temperature"],
 "subjects": [],
 "min_verification": "verified"}
```

- `artifact_id` is a stable slug and the DB primary key; reuse the same
  `artifact_id` to update an existing artifact, change it to create a new one.
- `title` becomes the page id. Name it the way a reader would search for it
  ("ALD Growth Per Cycle by Material"), not as a slug.
- `properties` are the table columns; rows are subjects.
- `subjects` empty = every subject that has data for these properties; list
  them to restrict or order the rows.
- `min_verification`: `verified` (default, only quote-verified cells) or
  `any` (include unverified / figure-digitized — use sparingly and only when
  the page makes the provenance explicit).

**Symbols go in as inline LaTeX — in `description` as well as in the
labels.** The data page typesets `$...$` with KaTeX, so a symbolic column
name or a `description` mentioning a symbol must be delimited:
`"description": "Fitted $\\delta$ in $I(Q) = c\\,Q^\\delta$ across
studies."`, not `"Fitted delta in I(Q)=c*Q^delta"`. The `description` is
a separate field from the row/column labels and is easy to miss on a
first pass. Never paste a Unicode maths character, and write ⟨c⟩ as
`$\\langle c \\rangle$` rather than `<c>` (raw angle brackets are escaped,
so `<c>` reaches the page as a literal `<c>`); the spec is JSON, so every
backslash is doubled. Row labels come from each claim's stored `subject` — fix a
mis-labelled one with `wikify data relabel <claim_id> --subject '<new>'
--run <bundle>` rather than editing `claims.db` directly.

**Read `missing_subjects` on every result.** A subjects filter is checked
per subject: any spec subject that matches no stored claim is listed there,
and a DRAFT build still renders from the ones that do resolve. Only a filter
where NO subject matches anything raises `subjects_filter_unmatched`.
Publishing is stricter. `consolidate --commit`, `data commit` and `data
rebuild` all refuse when the spec no longer resolves: a subject matching no
stored claim (`missing_subjects`), a NAMED subject contributing no row
(`subjects_without_rows`), a property contributing no cell
(`properties_without_cells`), or a zero-row table. They do NOT compare against
the published page, so a narrowing caused by real evidence movement — a new
conflicting paper, or a subject the spec does not name losing its last verified
claim — publishes normally. That is the evolving-artifact property. If it fires on a spelling you can see in
`data list`, the stored `subject_norm` keys are stale: run `wikify data
reindex --run <bundle>`.

A subject that DOES exist but whose property is not harvested yet, or whose
claims are all below `min_verification`, is not a `missing_subjects` error: a
DRAFT build renders it as an empty row-set and reports what it can in
`empty_columns`. Publishing that state is refused, because shipping it would
put a blank column or a dropped row on the page.

## Build and commit

Build, persist the spec, and write the wiki page + sidecar in one step. Pass
`--require-recall` so the data-recall gate is enforced at commit time:

```bash
wikify data consolidate spec.json --run <bundle> --commit --require-recall --format json
```

The spec may also be piped on stdin instead of a path. This writes
`wiki/data/<title>.md` (frontmatter `kind: data`, a markdown table whose
cells carry `[^dN]` markers, and a `## References` block in the standard
evidence-footnote format) plus a `.dataspec.json` sidecar holding the spec
and the backing claim ids. The renderer turns it into an HTML table and
folds its sources into `references.html` automatically.

Read the JSON result. `empty_columns` lists spec properties that matched no
stored claims (with `available_properties` to fix the spelling); `conflicts`
is the count of cells where papers disagree. Cells in conflict show **every**
reported value with its own citation plus a conflict note. Do not silently
pick a winner.

Without `--commit`, the artifact is stored as a draft; `wikify data commit
<artifact_id> --run <bundle> --require-recall` writes its page later under the
same gate.

## Keep it evolving

After later rounds add claims, refresh every committed artifact from its
stored spec:

```bash
wikify data rebuild --run <bundle>
```

`rebuild` re-derives each committed artifact's page (pass an `artifact_id` to
target one). Run it in the finalize step and on any re-entry so each table
reflects the current claim store.

**Check `skipped`.** An artifact whose spec subjects no longer resolve is
skipped rather than aborting the sweep — aborting would leave the artifacts
already rebuilt in that run written to disk while the rest kept stale pages.
`rebuild` returns `{"ok": <no skips>, "rebuilt": [...], "skipped":
[{artifact_id, error, message, ...}]}` — each skip carries the detail field its
`error` implies (`missing_subjects`, `subjects_without_rows`, `empty_columns`,
or none) — and **exits non-zero when anything was skipped**: a run that published nothing must not be
indistinguishable from a clean one. A non-empty `skipped` means those pages
are STALE: fix the spec or run `wikify data reindex`, then rebuild again.

## Data artifacts are a separate layer from the wiki graph

A data artifact is a first-class **rendered** page: it has a title, lives
under `wiki/data/`, carries references, and appears in the site — its own
"Data tables" section on the home page and sidebar, plus the aggregated
`references.html`. Treat it like a concept when naming and citing.

It is **not** a wiki-graph node. `data consolidate`/`commit` write it
directly under `wiki/data/`, not through `wiki commit`, so the wiki store
(`wiki.db`) never indexes it:

- `wiki show` / `wiki traverse` / `wiki find` and MCP `wiki_find` return
  `error="page_not_found"` for a data artifact. **This is expected** — do not
  retry on the wiki side.
- The round-trip surface for data artifacts is the `data` CLI noun:
  `data list-artifacts`, `data list`, `data show <claim_id>`, and
  `data query`. Use these to inspect or cite stored data, not the wiki tools.
