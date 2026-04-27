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

Cost is computed from `call` events. If a fact is needed for replay or
comparison, it belongs in the event ledger.
