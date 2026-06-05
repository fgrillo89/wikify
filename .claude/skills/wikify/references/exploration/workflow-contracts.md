# Workflow Contracts

Workflow skills own strategy:

- What to inspect next.
- Which sampling pattern (or `wikify-investigate` exploration pattern)
  to use.
- How much evidence is enough, including maturity-gate thresholds.
- When to spawn writers.
- Model tier and model id.
- Retry and escalation.
- Parallelism.
- Stop conditions, including the chunk-coverage target for
  `wikify-investigate`.

Core skills own mechanics:

- How to search corpus.
- How to search wiki.
- How to write a page from supplied context.
- How to mutate or inspect bundle state.
- How to run the recursive exploration patterns
  (`wikify-investigate-explore`).

Python owns deterministic validation and persistence. It does not own
strategy.

## Round contract (`wikify-investigate`)

A round is a logical step the editor takes between two `work tend`
calls. Each round writes exactly two envelopes to
`run/events.jsonl`:

```
{
  "type": "round_started",
  "actor": "editor",
  "stage": "round",
  "data": {"round": <N>}
}

{
  "type": "round_completed",
  "actor": "editor",
  "stage": "round",
  "data": {
    "round": <N>,
    "band_counts": {"ready": k, "growing": k, "stalled": k,
                    "new": k, "parked": k},
    "M1": <float|null>,
    "M3_modularity": <float|null>,
    "chunk_coverage_ratio": <float>,
    "dispatched_modes": ["WRITE", "GROW", "GAP", ...],
    "dispatched_patterns": ["P3", "P5", ...],
    "budget_used": <int>
  }
}
```

`M1` is `null` on rounds where no page was committed (M1 cannot move
otherwise). `M3_modularity` is `null` until `wiki rebuild` produces a
graph with at least 5 pages.

## Explorer envelope contract (per Task)

Returned by every `wikify-investigate-explore` Task:

```
{
  "target": "<slug | doc | pair | 'global'>",
  "pattern": "P1" | "P2" | "P3" | "P4" | "P5",
  "appended_chunks": <int>,
  "appended_concepts": <int>,
  "covered_docs_delta": {"<doc_id>": <int>, ...},
  "covered_chunks_delta": ["<chunk_id>", ...],
  "exploration_log_entry": {
    "round": <int>, "pattern": "...", "target": "...",
    "depth": <int>, "accepted": <int>
  },
  "stop_reason": "ok" | "budget_chunks_reached" |
                 "no_new_neighbours" | "depth_zero" |
                 "residual_empty" | "no_new_proposals",
  "tokens_in": <int>,
  "tokens_out": <int>,
  "model_id": "<model>"
}
```

The editor folds `covered_*_delta` into the notebook frontmatter
between Tasks via `notebook.merge_covered_docs` /
`notebook.append_exploration_log` (Python helpers). Tasks must NOT
write the notebook frontmatter from inside.

## Slug-disjoint dispatch invariant

`wikify-investigate` dispatches at most one Task per slug per round.
This is the only enforceable rule against `evidence.jsonl`
double-writes (`.claim` files cover concurrent `draft build` but not
concurrent evidence appends). The editor's dispatch planner builds
the plan slug-disjoint by construction; the explorer skill does not
need to coordinate with peers.
