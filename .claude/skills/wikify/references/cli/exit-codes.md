# Exit Codes

- 0: success.
- 1: validation or precondition failure.
- 2: lock or claim held.
- 3: budget exceeded.
- 4: stale claim broken by `work tend`.

Workflow skills decide retry, escalation, and stop behavior. Core skills
only surface the meaning of the code and the relevant inspection command.
