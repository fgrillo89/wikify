# Locking And Claims

Bundle mutation uses two coordination layers:

- Run lock: protects bundle-wide mutations.
- Concept claim: protects per-concept work in parallel writer loops.

Contention exits with code 2. Stale claims are handled by `wikify work
tend` according to TTL rules.

Workflows choose whether to wait, skip, retry, or tend. Core skills only
explain the mechanics.
