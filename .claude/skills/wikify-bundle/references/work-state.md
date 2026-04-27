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
