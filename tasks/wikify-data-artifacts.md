# wikify data artifacts — factual-data extraction + evolving data tables

Goal: capture factual figures/numbers (tables first, but any number found during
exploration) into a bundle-scoped claim store with verifiable provenance, let
writers consume them, and consolidate them into wiki-resident "data artifact"
tables that evolve as more data/papers arrive. Data artifacts are treated like
concepts: they live in the wiki, render to HTML with references, and are
re-derivable from a durable spec.

Design decisions (confirmed with user):
- Storage: **bundle-scoped** claim store (`<bundle>/claims.db`).
- Pipeline: **parallel** to concepts, with a new `data` page kind.
- Gathering: **both** piggyback (explorers harvest numbers from chunks they read)
  and a **dedicated** harvest pass targeting corpus table-assets + dense chunks.
- Gate: **hard** mechanical quote verification (tier-aware).
- Figures: caption numbers are first-class (verifiable text); plot digitization
  is supported as tier T3, flagged, opt-in.

## Architecture

```
exploration ──► claim store (claims.db, schema-on-read) ──► consolidation
   harvest          data_points + property_registry            (view spec)
   (P6 + piggyback)                                                 │
                                                                    ▼
writers ◄── data dossier slice (relevant claims)         data artifact page
                                                       (kind=data, .md + .dataspec.json)
                                                                    │
                                                                    ▼
                                                        render ──► HTML table + references.html
```

The claim store is the **source of truth**. A data-artifact table is a
**materialized view** over it: never edited by hand, always re-derivable from
its spec via `wikify data rebuild`. Adding claims/papers + rebuild = the table
evolves.

## Claim store (`src/wikify/data/store.py`, sqlite at `<bundle>/claims.db`)

`data_points` — one extracted fact (schema-on-read core):
- `claim_id` (content hash; idempotent dedup)
- assertion: `subject`, `subject_norm`, `property`, `property_norm`,
  `value_num` (canonical numeric or NULL), `value_text`, `unit`,
  `value_original`, `unit_original`, `uncertainty`, `value_type`
  (scalar|range|upper_bound|lower_bound|list|categorical)
- conditions (open): `conditions_json`, `method`
- provenance: `doc_id`, `chunk_id`, `locator`, `grounding_quote`,
  `quote_verified` (int), `source_kind` (table|text|caption|figure_caption|figure),
  `extraction_tier` (T1|T2|T3)
- assurance: `verification_status` (verified|unverified|conflict|figure_digitized|rejected),
  `confidence`, `extractor`, `round`, `created_at`

`property_registry` — thin typed catalog over the open property space:
- `property_norm` (pk), `canonical_unit`, `quantity_kind`, `description`,
  `n_points`, `aliases_json`

`data_artifacts` — consolidated views that become wiki pages:
- `artifact_id` (slug, pk), `title`, `description`, `spec_json`, `status`
  (draft|committed), `n_rows`, `created_at`, `updated_at`

`data_artifact_claims` — backing claims per artifact (re-derivation + refs):
- `(artifact_id, claim_id)` pk

## Verification gate (`src/wikify/data/verify.py`)

Tier-aware, mechanical:
- T1/T2 (text/table/caption): the value's number must be locatable in the source
  text (chunk text or asset caption). Tiers: exact substring → whitespace-collapsed
  → numeric-token containment (the reported number appears in the quote AND the
  quote appears in the source). Pass → `verified`; fail → `rejected`.
- T3 (figure plot digitization): no verbatim number to verify → `figure_digitized`
  (kept, never silently trusted). Requires a resolvable figure asset handle.
- Conflict: same `(subject_norm, property_norm)` with incompatible canonical
  values across docs → both flagged `conflict` at consolidation time.

## Consolidation (`src/wikify/data/consolidate.py`)

Input: a view spec `{title, subjects?, properties[], filters?, conditions_cols?}`.
Output: a pivot table (rows = subject, cols = property[+condition]), each cell
joined on canonical unit, carrying its backing `claim_id`s. Conflicts surfaced.
Records membership in `data_artifact_claims`. The spec is durable; the table is
a projection.

## Data-artifact page (deterministic, no LLM writer)

`wikify data commit <artifact>` writes:
- `wiki/data/<slug>.md` — frontmatter `kind: data` + markdown table (cells carry
  `[^eN]` markers in a Source column) + `## References` footnotes in the standard
  evidence format `[^eN]: <chunk_id> (<doc_id>, <locator>) > "<grounding_quote>"`.
- `wiki/data/<slug>.dataspec.json` — the view spec + backing claim_ids.

Because the page reuses the standard footnote/evidence format, the existing
renderer turns the markdown table into `<table>` and `_aggregate_references`
pulls the page's evidence into references.html with zero new render plumbing.

## Render changes (minimal)

- `PageKind` += `"data"`; `load_bundle` reads `wiki/data/`; `Bundle.ensure`
  creates it.
- `build_site`: segregate `data` pages, route them through the article renderer
  (table + footnotes already supported), output under `data/<slug>.html`, add a
  "Data" nav group, kind-aware infobox ("Data table", row/source counts).

## CLI (`wikify data`, `src/wikify/cli/data.py`)

- `data add <records.jsonl> --run --corpus` — ingest staged points, verify, dedup.
- `data list [--subject --property]` / `data show <claim_id|artifact>` — read.
- `data query --subject --property [--format json]` — claims as a table (writers).
- `data consolidate <spec.json> --run` — build artifact (draft).
- `data commit <artifact> --run` — write page + sidecar to wiki/data.
- `data rebuild [<artifact>] --run` — re-derive committed artifacts from specs.
- `data coverage --run` — n claims, verified ratio, subjects/properties covered.

## Skill + investigate integration

- `wikify-extract-data` (capability): harvest mechanics (table-assets, dense
  chunks, captions), emit staged records; called as dedicated P6 and piggybacked.
- `wikify-consolidate-data` (capability): pick a theme from the claim store,
  produce a view spec + commit the artifact.
- `wikify-investigate` SKILL: SENSE reads `data coverage`; a DATA wave harvests
  (P6) every round and consolidates when a subject×property cluster is dense
  enough; writers receive a data slice in the dossier.
- Draft builder: when verified claims exist for a page's subject, append a
  "Data" section to the dossier so the writer can cite numbers/embed a small table.

## Success criteria

1. Skill + code shipped, tests green, ruff clean.
2. Integrated into wikify-investigate.
3. Writers can consume data (dossier slice + `data query`).
4. Small e2e: ≥1 rendered data-artifact HTML with references; ≥1 concept page
   authored using data.
5. Final adversarial review passes; every valid finding fixed and re-reviewed
   until clean.
