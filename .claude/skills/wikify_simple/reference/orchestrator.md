# Orchestrator action catalog

The orchestrator is the LLM-policy decision maker. It runs at tier L (opus, locked) and picks ONE action each iteration of the extract loop. It is the only opus-tier caller in the pipeline (excluding handler-level escalation).

The orchestrator is invoked only when `--policy llm_policy`. With `--policy rule_policy` the sampler acts directly and this file is informational.

## Request shape
```json
{
  "run_id": "M_50000_seed0_create_all_20260411T120000",
  "n_pages": 42,
  "n_candidates": 17,
  "last_actions": ["jump_uniform", "walk_local", "walk_local"]
}
```

## Response shape
```json
{
  "name": "walk_local",
  "args": {"k": 4},
  "tokens_in": 1200,
  "tokens_out": 80
}
```

## Action catalog

### Sampling actions (consume budget)

| Action | Args | Effect | Cost |
|---|---|---|---|
| `walk_local` | `{k: int}` | k local similarity-walk steps from existing evidence chunks. | k × extract_tier |
| `jump_uniform` | `{n_docs: int}` | Pick n_docs documents uniformly, sample their top chunks (abstract + 2 top-degree). | n_docs × 3 × extract_tier |
| `jump_pagerank` | `{n_docs: int}` | Same as jump_uniform but pagerank-weighted over the doc graph. | n_docs × 3 × extract_tier |
| `jump_gap` | `{n_docs: int}` | Same but weighted by the current M1 coverage gap. | n_docs × 3 × extract_tier |

### Control actions (free)

| Action | Args | Effect |
|---|---|---|
| `set_allocation` | `{exploit_fraction: float}` | Reallocate remaining budget. `exploit_fraction` is the share going to write; the remainder goes to extract (minus 5% curate). Must be in [0, 1]. |
| `set_tier` | `{role: str, tier: str}` | Change the tier for future calls. `role` ∈ {`extract`, `write`, `edit`, `compact`}. `tier` ∈ {`S`, `M`, `L`}. `role="orchestrate"` is a no-op (locked). |

### Terminal action

| Action | Args | Effect |
|---|---|---|
| `done` | `{}` | Terminate the extract loop. The write loop still runs after. |

## Heuristics for picking actions

**Early in a run** (few `last_actions`, n_pages < 10):
- Prefer `jump_uniform` or `jump_pagerank` to spread breadth.
- Maybe one `set_tier` to lower write tier if you expect many writes.

**Mid-run** (n_pages 10-50):
- `walk_local` to deepen clusters.
- `jump_gap` to close coverage holes.
- Consider `set_allocation` to shift toward write as novelty drops.

**Late** (novelty plateauing):
- `set_allocation` with higher exploit_fraction.
- `set_tier` on writer to L for quality on the remaining pages.
- `done` when n_pages stops climbing across 5+ iterations.

## Escalation: NOT a dispatched action

When low-tier handlers (extract at S, write at M) are uncertain, they spawn a nested opus Task subagent INSIDE the handler invocation. This is local to the skill; it does NOT reach the orchestrator via file dispatch.

The escalation mechanism and the orchestrator are TWO DIFFERENT opus calls:
- **Orchestrator dispatch**: makes a policy decision (which action to pick next). Called once per extract loop iteration.
- **Handler escalation**: resolves a single content judgment (is this concept real? what should this sentence say?). Called 0+ times per handler invocation, only when the lower-tier model flags uncertainty.

Both run at tier L (opus). Both are billed through the cost meter. But they have different roles and different code paths.

## How control actions take effect

The `set_allocation` and `set_tier` actions mutate a `PolicyRuntime` object in `src/wikify_simple/distill/policy.py`. The pipeline reads the runtime on every iteration:
- After `set_allocation`, the next iteration re-splits the remaining budget using the new `exploit_fraction`.
- After `set_tier`, the next extract/write call uses the new tier.

Both changes are logged in `_calls.jsonl` so the post-run eval can see the orchestrator's trajectory.
