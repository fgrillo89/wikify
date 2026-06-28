---
name: bundle
description: Explain and use mechanical Wikify bundle operations: run lifecycle, work concepts, evidence, feedback, claims, draft build/check, validation, commit gates, projections, render, eval, events, locks, and failure codes. Use only for state inspection and mutation mechanics; strategy belongs in workflow skills.
allowed-tools: Bash(wikify run *) Bash(wikify work *) Bash(wikify draft *) Bash(wikify wiki *) Bash(wikify render *) Bash(wikify eval *)
---

# bundle

Use this skill for bundle state operations. A workflow decides what to
do; this skill explains how to do it safely through the CLI.

## Capability Surface

- Initialize, inspect, and close runs.
- List run events and understand cost/telemetry surfaces.
- List/show concepts, evidence, claims, and inbox state.
- Add concepts, evidence, and feedback records.
- Gather evidence deterministically with `build-evidence`.
- Claim and release concepts for mutation.
- Build and inspect drafts.
- Validate writer responses.
- Commit validated pages, or finalize them through the commit chain.
- Rebuild projections.
- Render and evaluate downstream artifacts.
- Interpret exit codes and recovery paths.

## Core Rule

Use the CLI for normal bundle reads and writes. Direct file reads are for
debugging repository code, not normal wikification workflows.

## Does Not Do

- Does not decide exploration order.
- Does not decide evidence thresholds.
- Does not decide writer tier or model.
- Does not decide when a workflow should stop.

## References

- `references/run-lifecycle.md`
- `references/work-state.md`
- `references/claims-and-locks.md`
- `references/draft-validation.md`
- `references/commit-and-projections.md`
- `references/render-and-eval.md`
- `references/failure-handling.md`
- `../reference/references/bundle/layout.md`
- `../reference/references/bundle/state.md`
- `../reference/references/bundle/events-ledger.md`
- `../reference/references/cli/exit-codes.md`
