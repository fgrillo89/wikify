---
name: wikify-maintain
description: Periodic maintenance pass over a Wikify bundle. Drains the inbox, expires stale claims, consolidates query feedback, and dispatches `wikify-refine` for any concept marked `needs_refine`. Use as a recurring sweep after baseline + query traffic. Status: stub — composition shape only.
allowed-tools: Bash(wikify *) Task
---

# wikify-maintain (stub)

Periodic housekeeping over an already-grown bundle. Composition
over deterministic primitives; no new CLI; no Python orchestration.

## Strategy decisions (override here)

- Cadence: triggered by the agent on user request or before a
  `wikify-render-eval` snapshot.
- Refine cap per pass: at most 4 concepts (parallel writers).
- Inbox consolidation threshold: drain whenever
  `work/inbox/*.jsonl` line count exceeds the cap documented in the
  agent prompt; otherwise leave for the next pass.

## Composition (no Python)

```
cd <b>
wikify work tend
wikify run list events --type query_feedback_created --tail 50
```

For each concept that surfaces with `needs_refine = true`
(`wikify work list --status needs_refine`), dispatch
`wikify-refine` against that slug. After all refines complete, run
the projection rebuild + snapshot:

```
wikify wiki build indexes
wikify wiki build graph
wikify wiki build vectors
wikify render --bundle <b> --format html
wikify eval   --bundle <b> --corpus <c>
```

## What this workflow does NOT do

- It does not extract new concepts. That is `wikify-baseline` or
  `wikify-guided-explore`.
- It does not commit raw drafts. Refines route through
  `wikify-refine` which goes through the standard validation gate.

## References

- [atoms.md](../wikify/references/atoms.md) — atom contracts.
- [escalation.md](../wikify/references/escalation.md) — retry policy.
