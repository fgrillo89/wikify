---
name: wikify-consolidate-data
description: Consolidator that turns the bundle claim store into evolving data-artifact tables. Picks a dense subject-by-property theme, writes a durable view spec, and commits a kind=data wiki page that renders as an HTML table with per-cell references and re-derives from the spec as more data lands.
allowed-tools: Bash(wikify *)
---

# wikify-consolidate-data

Turn scattered data points into a wiki-resident comparison table. A data
artifact is a **materialized view** over the claim store, described by a
durable spec. It is never hand-edited: re-running consolidation after new
claims (or new papers) arrive refreshes the table. That is what makes the
artifact evolve.

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
a routing problem in the claim store — prefer consolidating the dominant
spelling and escalate the alias merge to the editor rather than inventing a
mapping here.

## The view spec

```json
{"artifact_id": "ald-growth-per-cycle",
 "title": "ALD Growth Per Cycle by Material",
 "description": "Reported growth per cycle across ALD chemistries.",
 "properties": ["growth per cycle", "deposition temperature"],
 "subjects": [],
 "min_verification": "verified"}
```

- `artifact_id` is a stable slug (the DB key); `title` becomes the page id.
- `properties` are the table columns (rows are subjects).
- `subjects` empty = every subject that has data for these properties;
  list them to restrict/order.
- `min_verification`: `verified` (default, only quote-verified cells) or
  `any` (include unverified / figure-digitized — use sparingly and only when
  the page makes the provenance explicit).

## Build and commit

```bash
# Build + persist the spec, then write the wiki page + sidecar in one step:
wikify data consolidate spec.json --run <bundle> --commit --format json
```

This writes `wiki/data/<title>.md` (frontmatter `kind: data`, a markdown
table whose cells carry `[^dN]` markers, and a `## References` block in the
standard evidence-footnote format) plus a `.dataspec.json` sidecar holding
the spec + backing claim ids. The renderer turns it into an HTML table and
folds its sources into `references.html` automatically.

Cells where papers disagree are emitted as **conflicts**: every reported
value is shown with its own citation, and a note records the conflict count.
Do not silently pick a winner.

## Keep it evolving

After later rounds add claims, refresh every committed artifact from its
stored spec:

```bash
wikify data rebuild --run <bundle>
```

Run `rebuild` in the finalize step (and any re-entry) so each data table
reflects the current claim store. The spec is the durable thing; the table
is always re-derived.

## Treat artifacts like concepts

A data artifact is a first-class wiki page: it has a title, lives under
`wiki/data/`, carries references, and appears in the rendered site — its
own "Data tables" section on the home page and sidebar, plus the aggregated
`references.html`. (It is written directly by `data commit`, not through
`wiki commit`, so it is not indexed in `wiki.db` / MCP `wiki_find`.) Name it
the way a reader would search for it ("ALD Growth Per Cycle by Material"),
not as a slug.
