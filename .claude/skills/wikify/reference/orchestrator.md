# Orchestrator action catalog

The orchestrator is the LLM-policy decision maker. It runs at tier L (opus, locked) and picks ONE action each iteration of the extract loop. It is the only opus-tier caller in the pipeline (excluding handler-level escalation).

The orchestrator is invoked only when `--mode guided`. With `--mode scripted` the sampler acts directly and this file is informational.

## Request shape
```json
{
  "run_id": "M_50000_seed0_create_all_20260411T120000",
  "n_pages": 42,
  "n_candidates": 17,
  "last_actions": ["jump_uniform", "walk_local", "walk_local"],
  "sampler_snapshot": {
    "top_gap_chunks": [
      {"chunk_id": "p3/c07", "doc_id": "p3", "residual": 0.95}
    ],
    "doc_coverage": {"p1": 4, "p2": 2},
    "content_stats": {"n_chunks": 1200, "n_seen": 340}
  }
}
```

`sampler_snapshot` is present when the pipeline runs Phase 3+. It contains:
- `top_gap_chunks`: top-20 unseen chunks by coverage residual (highest residual = least covered).
- `doc_coverage`: `{doc_id: n_chunks_seen}` for docs with any reads.
- `content_stats`: `{n_chunks, n_seen}` aggregate counts.

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

### Targeted chunk picking (Phase 3)

| Action | Args | Effect | Cached? |
|---|---|---|---|
| `pick_chunks` | `{chunk_ids: list[str], reason: str}` | Bypass the deterministic sampler; dispatch exactly these chunks. Chunks already in `seen_chunks` are silently filtered out. `reason` is recorded in `policy_events` for telemetry. | No |

Use `pick_chunks` after calling the in-handler tools `semantic_query` and `inspect_pages` to identify which chunks to target.

### In-handler tools (Phase 3)

These are callable inside the orchestrator handler. They return data to inform the `pick_chunks` decision. They are NOT dispatched actions.

| Tool | Args | Returns |
|---|---|---|
| `semantic_query` | `{query: str, k: int, scope: "all"\|"unseen"\|"page:<id>"}` | `[{chunk_id, doc_id, score, is_seen}]` sorted by cosine similarity |
| `inspect_pages` | `{page_ids: list[str] \| null}` | `[{id, title, n_evidence, has_body}]` for named pages or all pages |

Python helper: `wikify.distill.sampler.semantic_query_chunks(state, query_vec, k, scope)`.

### Cheap fallback: deterministic sampling actions (cached for up to 8 batches)

| Action | Args | Effect | Cost |
|---|---|---|---|
| `walk_local` | `{k: int}` | k local similarity-walk steps from existing evidence chunks. | k x extract_tier |
| `jump_uniform` | `{n_docs: int}` | Pick n_docs documents uniformly, sample their top chunks (abstract + 2 top-degree). | n_docs x 3 x extract_tier |
| `jump_pagerank` | `{n_docs: int}` | Same as jump_uniform but pagerank-weighted over the doc graph. | n_docs x 3 x extract_tier |
| `jump_gap` | `{n_docs: int}` | Same but weighted by the current M1 coverage gap. | n_docs x 3 x extract_tier |
| `jump_figures` | `{k: int}` | Pop the top-k highest-residual caption chunks from the dedicated caption heap. Use to ensure figure captions are fed to the extractor. | k x extract_tier |

### Control actions (free, never cached)

| Action | Args | Effect |
|---|---|---|
| `set_allocation` | `{exploit_fraction: float}` | Reallocate remaining budget. `exploit_fraction` is the share going to write; the remainder goes to extract (minus 5% curate). Must be in [0, 1]. |
| `set_tier` | `{role: str, tier: str}` | Change the tier for future calls. `role` in {`extract`, `write`, `edit`, `compact`}. `tier` in {`S`, `M`, `L`}. `role="orchestrate"` is a no-op (locked). |

### Terminal action

| Action | Args | Effect |
|---|---|---|
| `done` | `{}` | Terminate the extract loop. The write loop still runs after. |

## Heuristics for picking actions

**Early in a run** (few `last_actions`, n_pages < 10):
- Prefer `jump_uniform` or `jump_pagerank` to spread breadth. No need for `pick_chunks` yet.
- Maybe one `set_tier` to lower write tier if you expect many writes.

**Mid-run** (n_pages 10-50):
- `walk_local` to deepen clusters.
- `jump_gap` to close coverage holes.
- Use `semantic_query` + `pick_chunks` when you want to target a specific theme or deepen a thin page.

**Late** (novelty plateauing):
- `set_allocation` with higher exploit_fraction.
- `set_tier` on writer to L for quality on the remaining pages.
- `pick_chunks` from `sampler_snapshot.top_gap_chunks` to close the last coverage gaps.
- `done` when n_pages stops climbing across 5+ iterations.

## Escalation: NOT a dispatched action

When low-tier handlers (extract at S, write at M) are uncertain, they spawn a nested opus Task subagent INSIDE the handler invocation. This is local to the skill; it does NOT reach the orchestrator via file dispatch.

The escalation mechanism and the orchestrator are TWO DIFFERENT opus calls:
- **Orchestrator dispatch**: makes a policy decision (which action to pick next). Called once per extract loop iteration.
- **Handler escalation**: resolves a single content judgment (is this concept real? what should this sentence say?). Called 0+ times per handler invocation, only when the lower-tier model flags uncertainty.

Both run at tier L (opus). Both are billed through the cost meter. But they have different roles and different code paths.

## How control actions take effect

The `set_allocation` and `set_tier` actions mutate a `PolicyRuntime` object in `src/wikify/distill/policy.py`. The pipeline reads the runtime on every iteration:
- After `set_allocation`, the next iteration re-splits the remaining budget using the new `exploit_fraction`.
- After `set_tier`, the next extract/write call uses the new tier.

Both changes are logged in `_calls.jsonl` so the post-run eval can see the orchestrator's trajectory.

## Vision on demand (future capability)

`vision_on_demand` is a documented future capability. When the extract handler emits `extra: {needs_vision: true}` for a caption chunk, the pipeline logs the request to `_run.json::vision_requests`. No real vision binding exists today; a future binding will process these requests by calling a multimodal model on the actual image file. The telemetry slot is already live.
