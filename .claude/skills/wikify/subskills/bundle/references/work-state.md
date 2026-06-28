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

`work add evidence` emits an `evidence_added` event and accepts
`--round <n>` to stamp the round on that event. `work build-evidence`
appends records but does NOT emit `evidence_added`. Growth-stall
maturity keys off `evidence_added` events scoped to the latest round, so
a slug grown only through `build-evidence` must have the event emitted by
the caller (for example `work add evidence --round <n>`) or the stall
gate never advances.
