# Workflow Contracts

Workflow skills own strategy:

- What to inspect next.
- Which sampling pattern to use.
- How much evidence is enough.
- When to spawn writers.
- Model tier and model id.
- Retry and escalation.
- Parallelism.
- Stop conditions.

Core skills own mechanics:

- How to search corpus.
- How to search wiki.
- How to write a page from supplied context.
- How to mutate or inspect bundle state.

Python owns deterministic validation and persistence. It does not own
strategy.
