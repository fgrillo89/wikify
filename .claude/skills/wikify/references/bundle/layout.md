# Bundle Layout

```text
<bundle>/
  run/
  work/
  wiki/
  derived/
```

- `run/` stores lifecycle state, events, lock, and captured IO.
- `work/` stores in-flight concepts, evidence ledgers, inbox records,
  and claims.
- `wiki/` stores committed human-facing pages.
- `derived/` stores rebuildable projections, render output, and eval
  reports.

Canonical state lives in `run/`, `work/`, and `wiki/`. `derived/` is
rebuildable.
