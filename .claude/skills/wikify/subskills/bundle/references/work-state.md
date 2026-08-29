# Work State

Use `wikify work` for in-flight state.

```bash
wikify work list [--run <bundle>]
wikify work show <concept> [--run <bundle>] [--full]
wikify work add concept "<title>" [--kind article|person] [--aliases <json>]
wikify work add evidence <concept> --records <jsonl-path>
wikify work add feedback <kind> --record <json-or-path>
wikify work set <concept> [--status <status>] [--needs-refine]
wikify work tend [--run <bundle>]
```

`work tend` performs deterministic housekeeping: expire stale claims,
deduplicate evidence, consolidate inbox records, and regenerate
`work/index.md`.

Workflows decide when tending is necessary.

## Gather Evidence

`work build-evidence` gathers evidence for a concept from its
`seed_doc_handles` plus `corpus find --rank all` with structural
exclusions, then appends active records.

```bash
wikify work build-evidence <concept> --corpus <corpus> [--target N] [--from-ids <ids>|@-]
```

`--from-ids <a,b,c>` (or `@-` to read a JSON list of
`{"chunk_id", "score"?, "quote"?}` entries from stdin) bypasses the
seed/find phases and appends the supplied ids after boilerplate,
excluded-kind, never-cite, length, and quote-grounding checks. A
`chunk:<short>` handle resolves to its canonical id.

## Evidence Events

All three commands that append to `evidence.jsonl` — `work add
evidence`, `work build-evidence`, and `work tend` (the inbox drain) —
emit an `evidence_added` event when the slug actually grew. The first two key
off the records they append; `work tend` keys off NET growth, since it dedups
later in the same pass and a re-suggested chunk must not reset the timer.
`work add evidence` also accepts `--round <n>` to stamp the round on the
event. Growth-stall maturity keys off `evidence_added` events scoped to
the latest round, so no separate call is needed after a gather — and a
duplicate event emitted by hand would reset the stall timer for a round
the slug did not grow. To record growth that reached the ledger through
none of the three, use `wikify run record-event --type evidence_added
--concept-id <slug>`.

## Evidence Recall

`work concept-recall <slug> --corpus <c> --run <b>` scores how much of the
corpus's most-relevant literature a slug's evidence actually covers. Beyond
`recall_ok` / `missing_docs` / `max_doc_share`, the payload carries the
publication-era criterion and its own applicability:

- `year_buckets` / `empty_buckets` — era coverage over dated candidates.
- `year_coverage` — share of candidate docs carrying a parseable year.
- `year_gate_applied` — whether the era criterion is ENFORCED. Below a
  `year_coverage` of 0.6 it is not: the buckets rest on too small a dated
  slice to describe breadth, so an empty one reports the state of the metadata
  rather than of the page. A corpus of arXiv/SSRN PDFs with `documents.year`
  mostly null lands here. The bucket IS still clearable — totals count only
  dated candidates, and `missing_docs` lists them — so this is a noise
  threshold, not an impossibility one.

`empty_buckets` is reported either way, so read `year_gate_applied` before
acting on it. When the gate is skipped, the fix is to backfill document
metadata, not to gather more evidence.
