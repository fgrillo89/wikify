# Run Lifecycle

Use `wikify run` for bundle lifecycle and telemetry inspection.

```bash
wikify run init --bundle <bundle> --corpus <corpus> --strategy <name>
wikify run show [--run <bundle>] [--full]
wikify run list events [--run <bundle>] [--type <event-type>]
wikify run close --status completed|failed [--run <bundle>]
```

`run/state.json` stores durable run identity, strategy label, corpus
path, budget state, and coarse status. `run/events.jsonl` is the
append-only event ledger.

Workflows own strategy names, budgets, and close criteria.
