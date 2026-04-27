# Failure Handling

CLI exit codes:

- 0: success.
- 1: validation or precondition failure.
- 2: lock or claim held.
- 3: budget exceeded.
- 4: stale claim broken by `work tend`.

## Response Rules

- Validation failure: inspect command output and relevant
  `validation.json`; workflow decides retry/escalation.
- Claim contention: skip, retry later, or run `work tend` if stale claim
  is plausible.
- Budget exceeded: stop strategy work and close or report partial state.
- Projection stale: rebuild projections if the workflow needs search,
  render, or eval freshness.
