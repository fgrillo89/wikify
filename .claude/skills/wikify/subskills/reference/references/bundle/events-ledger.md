# Events Ledger

`run/events.jsonl` is the append-only record of what happened during a
run. It supports replay, cost analysis, and strategy comparison.

Events use a common envelope:

```text
schema_version, event_id, run_id, type, at, actor, data
```

Common event types include:

- `cli_invoked`
- `stage_changed`
- `concept_created`
- `evidence_added`
- `draft_created`
- `call`
- `validation_completed`
- `page_committed`
- `inbox_consolidated`
- `budget_exceeded`
- `run_closed`
- `page_recall_cleared`
- `editor_ruling`

`page_recall_cleared` records that a slug passed (or was excused from) the
evidence-recall gate; `draft finalize --require-recall` refuses to commit an
article without a FRESH one. Payload carries `recall_ok` or `exhausted`.

`editor_ruling` records a deliberate editor decision that overrides or
accepts a non-blocking signal, so the choice is auditable rather than
invisible: shipping a page on genuinely thin evidence rather than padding it
with fabricated attribution, or falling back to relevance-based seeding when
`degenerate_metrics` shows the PageRank ranking carries no signal. Carry a
`ruling` key plus whatever made the call (`{"ruling": "thin_evidence",
"n_docs": 3, "reason": "..."}`).

Cost is computed from `call` events. If a fact is needed for replay or
comparison, it belongs in the event ledger.
