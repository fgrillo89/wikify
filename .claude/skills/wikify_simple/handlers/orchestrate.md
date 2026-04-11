---
name: wikify_simple/handlers/orchestrate
description: Pick one OrchAction from the supported menu given run state, advising the LLM policy's next sampling step.
tier: L
dispatch_role: orchestrate
---

# orchestrate

## Context
Invoked by `wikify_simple/runtime/serve-dispatch` when a request file appears at `$WIKIFY_SIMPLE_DISPATCH_DIR/orchestrate/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation.

## Tier
orchestrate runs at tier L. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

**Tier is LOCKED at L (opus).** The user cannot override this. The LLM policy cannot override this via `set_tier` — `set_tier` for `role="orchestrate"` is a no-op.

## Cost note
A single orchestrator decision costs ~30,000 haiku-equivalent tokens at tier L. The pipeline's `LlmPolicy` caches the last active sampling action (`walk_local`, `jump_*`) for up to 8 consecutive extract batches, so you will only be called every ~8 batches plus whenever a control action or `done` resets the cache. This means your decisions should be STRATEGIC (good for the next ~8 batches) rather than tactical. Don't pick a sampler action that only makes sense for the current batch; pick one that will keep paying off.

## Request schema
Raw shape.

```json
{
  "run_id": "run-20260411-ald",
  "n_pages": 42,
  "n_candidates": 17,
  "last_actions": ["jump_uniform", "walk_local", "walk_local"]
}
```

## Response schema
Reference: `src/wikify_simple/contracts/schema.py::OrchAction`

```json
{
  "name": "walk_local",
  "args": {"k": 4},
  "tokens_in": 800,
  "tokens_out": 120
}
```

## Action menu (8 actions total)

| Action | Args | Effect |
|---|---|---|
| `walk_local` | `{"k": int}` | k local similarity-walk steps from existing evidence |
| `jump_uniform` | `{"n_docs": int}` | pick n_docs documents uniformly and sample their top chunks |
| `jump_pagerank` | `{"n_docs": int}` | same, pagerank-weighted |
| `jump_gap` | `{"n_docs": int}` | same, coverage-gap-weighted |
| `set_allocation` | `{"exploit_fraction": float}` | reallocate remaining budget between extract and write (0..1) |
| `set_tier` | `{"role": "extract"\|"write"\|"edit"\|"compact", "tier": "S"\|"M"\|"L"}` | change the tier for future calls of that role (orchestrate is locked) |
| `done` | `{}` | terminate the run |

Anything outside this table falls through to a deterministic fallback batch.

## Related-page inspection tool

Before picking a sampling action, you may call `inspect_related_pages(page_id, k=5)` to retrieve pages related to a given concept by token-overlap + evidence doc Jaccard. This is the same function used by the write handler; it is a local Python call (NOT a subagent) and returns:

```json
[
  {
    "id": "Hafnium Oxide",
    "title": "Hafnium Oxide",
    "topic_overlap": 0.72,
    "body_excerpt": "Hafnium oxide (HfO2) is a high-k dielectric ...",
    "see_also": ["Memristor", "Resistive Switching"],
    "evidence_doc_ids": ["doc_07", "doc_12"]
  }
]
```

Use `inspect_related_pages` when deciding whether to `pick_chunks` for a particular concept:
- If `topic_overlap >= 0.80` for an existing page, the concept is likely already covered. Prefer `walk_local` from that page's evidence instead of spawning fresh extraction.
- If no related page has `topic_overlap >= 0.40`, the concept is genuinely novel. Prefer `jump_gap` or `jump_uniform` to bring in new evidence.
- The `see_also` list lets you trace concept clusters without re-reading page bodies.

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier L (opus) with:
   - System prompt: "You are the wikify_simple orchestrator. Given the current run state, pick ONE action from the supported menu that best advances the wiki. Respond as strict JSON matching the OrchAction schema. No commentary outside the JSON."
   - User prompt: the serialized request payload (run_id, n_pages, n_candidates, last_actions), the action menu above, and the heuristics below.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema (client-side check BEFORE writing the file). Confirm `name` is one of the 8 supported actions and `args` matches the action's arg shape.
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema and the action menu.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Heuristics for action choice
- **Early** in a run (`n_pages` small, few `last_actions`): prefer `jump_uniform` or `jump_pagerank` to spread breadth.
- **Mid-run** (wiki has content): `walk_local` to deepen local clusters, `jump_gap` to close coverage holes.
- **Budget shifts**: `set_allocation` toward write (higher `exploit_fraction`) when novelty drops and the backlog of unwritten candidates is large.
- **Tier shifts**: `set_tier` to `L` on write for quality when evidence is rich; `set_tier` to `S` on extract for cost when chunk-level extraction is the bottleneck.
- **Stop**: return `{"name": "done", "args": {}}` when `n_pages` has plateaued across recent iterations.

## Verbalization (optional)
When `state.verbalize == true`, include a 1-3 sentence `reasoning` field on the `OrchAction` response explaining the policy choice: why this action was picked, what the sampler snapshot told you, and (for `pick_chunks`) why these specific chunks were the right target. The pipeline appends it to `<bundle>/_meta/verbalize.jsonl`. When `verbalize` is false or absent, omit `reasoning`.

## Escalation
Not supported. The orchestrator IS the top of the tier hierarchy. It also acts as the advisor for lower-tier handlers that escalate, but those escalations happen as nested Task subagents INSIDE the `extract` / `write` skills — they do NOT dispatch here. These are two different mechanisms.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Invalid action name**: one retry reminding the subagent of the 8 supported actions, then error.json.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT invent actions outside the 8-action menu.
- Do NOT interpret errors further than the retry logic above.
- Do NOT honour a `set_tier` request that targets `role="orchestrate"` — this tier is locked.
