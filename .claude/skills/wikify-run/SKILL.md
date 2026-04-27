---
name: wikify-run
description: Run-level execution control for a Wikify bundle. Use to initialise a fresh bundle, inspect run state, list events, take or release the run lock, or close the run. Mutating commands write through `run/state.json` and append to `run/events.jsonl`.
allowed-tools: Bash(wikify run *)
---

# wikify-run

Run-level execution control. `run init` creates the bundle layout
and the first events; `run close` writes `run_closed` with the final
status. The run lock fences any longer skill section that needs
exclusive access to the bundle.

## Commands

```
wikify run init   --bundle <b> --corpus <c> [--strategy <label>]
                  [--target-haiku-eq N] [--format text|json]
wikify run show   [--run <b>] [--detail|--full] [--format text|json]
wikify run list   events [--run <b>] [--tail N] [--type <t>] [--format text|json]
wikify run lock   [--run <b>] [--owner <id>] [--ttl-seconds N] [--format text|json]
wikify run unlock [--run <b>] [--format text|json]
wikify run close  [--run <b>] [--status completed|failed|abandoned] [--format text|json]
wikify run set    [--run <b>] [--target-haiku-eq N] [--strategy-note <s>] [--format text|json]
```

`--strategy` is a free-form workflow label (`baseline`, `guided`,
`free`, `query`); the agent picks. No Python branch reads it.

## Inputs

- `--bundle <b>` (init only) — directory to populate with the bundle
  layout. Must be empty or a fresh path.
- `--corpus <c>` (init only) — corpus path recorded in `state.json`.
- `--run <b>` (everything else) — bundle override; default is CWD.

## Outputs

- `run/state.json` — durable run state (RunState).
- `run/events.jsonl` — append-only event ledger.
- `run/lock` — atomic file lock with TTL.
- `run/io/<event_id>.{stdout,stderr}.txt` — captured CLI IO.

## When to use

- Bootstrap a fresh bundle before any other work.
- Read current run identity / strategy / budget.
- Tail the event log to debug a stalled workflow.
- Take an explicit lock around a multi-step section that must not
  race with another agent.
- Mark the run completed/failed/abandoned at the end of a workflow.

## Exit codes

`run lock` exits 2 on contention. Other commands exit 1 on
validation failure.

## References

- [schemas.md](../wikify/references/schemas.md) — RunState + Event
  envelope + `cli_invoked` IO transcripts.
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md) —
  full grammar.
- [atoms.md](../wikify/references/atoms.md) — `run init` / `run close`
  pre/post-conditions.
