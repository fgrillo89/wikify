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

## Build and commit

Build, persist the spec, and write the wiki page + sidecar in one step:

```bash
wikify data consolidate spec.json --run <bundle> --commit --format json
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
<artifact_id> --run <bundle>` writes its page later.

## Keep it evolving

After later rounds add claims, refresh every committed artifact from its
stored spec:

```bash
wikify data rebuild --run <bundle>
```

`rebuild` re-derives each committed artifact's page (pass an `artifact_id` to
target one). Run it in the finalize step and on any re-entry so each table
reflects the current claim store.

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
