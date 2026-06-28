# Claims And Locks

Bundle mutation is protected by locks and claims.

## Run Lock

The run lock protects bundle-wide mutations such as commit and close.
Most CLI commands acquire it internally when needed.

## Concept Claim

Use a concept claim before parallel work on one concept:

```bash
wikify work claim <slug> --ttl-seconds <seconds>
wikify work release <slug>
```

Claim contention exits with code 2. A workflow may skip, retry later, or
call `work tend` if it suspects stale claims.

Do not break live claims manually unless the workflow explicitly owns
that recovery path.
