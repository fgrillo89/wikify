---
name: wikify/reference/tiers
description: Model tier mapping (S/M/L) and per-role default tiers for wikify subagent calls.
---

# Tiers

Every model call Wikify makes is tagged with a tier. The tier determines the
model family; the workflow's strategy and budget together determine when to
escalate.

## Tier mapping

- **S** — haiku-class small model. Cheap, fast, for structured extraction and light validation.
- **M** — sonnet-class medium model. The default for generative writing tasks.
- **L** — opus-class large model. Reserved for escalation — uncertainty, cross-domain synthesis, hard validation failures.

The exact model ID per tier is set at run time by the skill, not baked into
Python. The budget and telemetry in `_run.json` / `_calls.jsonl` use
`haiku_eq` units normalised across tiers (see `src/wikify/meter.py`).

## Per-role defaults

The role names in this table are the values of the `Role` enum in
`src/wikify/types.py` — those are the only strings `wikify meter
record --role <r>` accepts.

| Role | Default tier | Rationale |
|---|---|---|
| extractor | S | structured extraction from one chunk, low creative load |
| writer | M | encyclopedic prose, evidence synthesis across a single page |
| editor | M | targeted revision with preserved structure |
| compactor | S | length reduction without information loss |
| orchestrator | L | planning decisions with wide context |

`escalate` is not a role; it is a tier-bump on the existing role. See
`escalation.md`.

## Override mechanics

- **Per-request override.** Workflows that need a non-default tier set `tier` on the request payload (baseline does not; scripted-M may; guided does routinely).
- **Skill-level override.** A workflow skill may pass `--tier L` or equivalent to a subcommand that supports it. Document the override explicitly — silent tier bumps distort cost measurements.
- **Escalation.** Validation failures and explicit `escalate: true` outputs from a subagent trigger a nested Task call at tier L with the original request plus the escalation reason. See `escalation.md`.

## Budget accounting

- The budget is measured in `haiku_eq` units. A tier-M call is multiplied by the configured M-multiplier; a tier-L call by the L-multiplier. Multipliers live in `src/wikify/config.py`.
- The workflow reads `session.budget.haiku_eq_target` and updates `haiku_eq_spent` after each call (via `wikify session update` or through a scratch-to-bundle commit that bundles the meter record).
- Budget exhaustion is a stopping criterion for autoresearch-style loops (guided, later scripted variants). Baseline's deterministic page set is finite, so budget is a soft ceiling rather than a loop-termination trigger.

## Tier is a control parameter, not a capability claim

The tier determines which model family to spend on, not which capability to
claim. If tier-M output fails validation, the correct response is to escalate
(tier L) or to fail the iteration — not to claim the output is "good enough
because we asked for M."
