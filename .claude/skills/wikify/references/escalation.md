---
name: wikify/references/escalation
description: When and how to escalate a subagent call from the default tier to tier L.
---

# Escalation

Escalation is the fallback mechanism for calls that hit validation failures
or reach the subagent's confidence floor. It is NOT a retry-with-bigger-model
pattern for every failure. Use it sparingly and record it.

## When to escalate

Escalate to tier L when one of the following holds:

- A tier-S or tier-M call fails structural or schema validation twice at its default tier.
- The subagent explicitly returns `{"escalate": true, "reason": "..."}` in a top-level `escalation` field on its response.
- The evidence list is internally contradictory and a single reconciliation decision is needed.
- Cross-domain synthesis is required (multiple distinct sub-topics must be reconciled into one page).
- The editor brief calls for synthesis beyond any single source.

## When NOT to escalate

- First validation failure at the default tier — retry once with a stricter prompt that names the specific constraint that failed.
- Empty output on a trivially extractable chunk — likely a prompt or schema problem, not a capability gap.
- Budget exhaustion — escalation makes a call more expensive, not cheaper. Stop the loop instead.
- Non-model errors (file-not-found, lock-held, schema-mismatch). Fix the error; escalation does not help.

## How to escalate

Escalation happens INSIDE the skill invocation. It does NOT write a separate
dispatch artifact. It DOES cost more tokens — the tier-L call bills at the
opus-class multiplier in `haiku_eq` units.

Mechanics:

1. Spawn a nested `Task` subagent at tier L with:
   - The original request payload verbatim.
   - The escalation reason (from the tier-M subagent, or from the skill's own retry-exhaustion record).
   - The same validator constraints attached to the user prompt.
2. Use the tier-L subagent's output as the final response for the request.
3. Record the escalation in a `meta.escalated_from: <default_tier>` field on the response so cost-meter attribution is preserved.
4. Persist the escalation as a `meta.escalated_from` field on
   `response.json`, and append a free-form note via
   `wikify work add feedback evidence --record <escalation-note.json>`
   if the escalation rationale should survive cross-run.

## Retry vs escalate

The default pattern for a single call is:

1. First call at default tier.
2. On validation failure: retry once at the same tier with a stricter prompt.
3. On second failure: escalate to tier L once.
4. On third failure: mark the artifact as `failed` in session state. Do not loop.

This matches the "one retry, then escalate once, then fail" policy implicit
in the pre-pivot handler skills.

## Telemetry

Every escalation leaves two traces:

- A `type: "call"` event in `run/events.jsonl` with `tier: "L"` and
  `role: <original-role>` (the per-call cost record).
- A `meta.escalated_from` field on `response.json` so the verdict
  shape carries the context.

The workflow may also append an `inbox_suggestion_created` or a
custom `stage_changed` event to `events.jsonl` if escalation counts
need to be aggregable for guided/free strategies. Baseline does not
track this.

## What escalation does not solve

- A systematic prompt failure reproducing across all tiers — fix the prompt.
- A schema mismatch between Python and the reference file — fix the reference and the validator.
- A context budget that is too small — enlarge the context or trim the evidence; do not pay for tier L to compensate.
