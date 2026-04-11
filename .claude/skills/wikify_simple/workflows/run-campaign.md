---
name: wikify_simple/workflows/run-campaign
description: Run an LLM-policy distillation campaign where the orchestrator (opus) decides allocation, tiers, and sampling actions.
---

# run-campaign

User-facing workflow for the LLM-driven scenario. The user sets budget and iterations; the orchestrator decides everything else via its action menu.

## Required setup
- `--binding file_dispatch` (the only binding that exposes an orchestrator).
- `WIKIFY_SIMPLE_ALLOW_NETWORK=1` in the environment.
- A parallel Claude session running `wikify_simple/runtime/serve-dispatch` to handle file dispatch.

## Inputs

| Parameter | Default | Description |
|---|---|---|
| `corpus` | `data/corpus` | Ingested corpus. |
| `out` | `data/wikis/<run_id>` | Bundle output directory. |
| `strategy` | `M` | E/M/X — controls the SAMPLER (not the policy). The orchestrator overrides schedule and tiers mid-run. |
| `iterations` | `1` | How many distill calls. First is `create`, rest are `refine`. |
| `budget_per_iteration` | `50000` | Haiku-equivalent tokens per iteration. |
| `seed` | `0` | RNG seed (increments per iteration). |
| `field` | auto-detect | Writer field guide. |
| `artifact` | `wiki_concept` | Writer artifact template. |
| `render_html` | `true` | Render HTML after the final iteration. |
| `run_eval` | `true` | Eval after the final iteration. |

## Locked defaults (NOT user-settable)
- `policy = llm_policy`
- `binding = file_dispatch`
- `orchestrate_tier = L (opus)` — the orchestrator is always top-tier; this is what makes the escalation model work.
- Initial tiers: `extract_tier=S`, `write_tier=M`, `edit_tier=M`, `compact_tier=S`. The orchestrator may override any of these mid-run via `set_tier`.
- Initial allocation: the strategy's default `exploit_fraction`. The orchestrator may override mid-run via `set_allocation`.

## What the orchestrator can do

Each extract iteration, the orchestrator picks ONE action:

| Action | Args | Effect |
|---|---|---|
| `walk_local` | `{k: int}` | take k local similarity-walk steps from existing evidence |
| `jump_uniform` | `{n_docs: int}` | pick n_docs uniformly, sample their top chunks |
| `jump_pagerank` | `{n_docs: int}` | pagerank-weighted |
| `jump_gap` | `{n_docs: int}` | coverage-gap-weighted |
| `set_allocation` | `{exploit_fraction: float}` | reallocate remaining budget (0..1) |
| `set_tier` | `{role: "extract"|"write"|"edit"|"compact", tier: "S"|"M"|"L"}` | change tier for future calls |
| `done` | `{}` | terminate |

## Escalation model
- Low-tier handlers (extract at S, write at M) can escalate to opus INSIDE their handler invocation — this spawns a nested opus Task subagent with the original request + an uncertainty reason. The escalation does NOT dispatch a new file; it is local to the skill.
- Escalation is for content judgment (ambiguous concept? contradictory evidence? cross-domain synthesis?).
- The orchestrator dispatched via `orchestrate/*.request.json` is a DIFFERENT opus call — it is the policy brain for sampling and budget decisions.

## Steps
1. Verify the corpus exists and `WIKIFY_SIMPLE_ALLOW_NETWORK=1` is set.
2. Pick an explicit bundle path: `BUNDLE=data/wikis/campaign_<strategy>_<timestamp>`.
3. Verify a serve-dispatch session is running (or instruct the user to start one in parallel).
4. For `i` in `1..iterations`:
   a. `iteration_op = "create" if i == 1 else "refine"`.
   b. Run:
      ```
      uv run python -m wikify_simple.cli distill \
        --strategy {strategy} --policy llm_policy --binding file_dispatch \
        --budget {budget_per_iteration} --seed {seed+i-1} \
        --iteration {iteration_op} \
        --corpus {corpus} --bundle $BUNDLE \
        [--field {field}] --artifact {artifact}
      ```
      `--bundle` forces every iteration to write to the SAME path.
   c. Wait for the Python process to exit. If it hangs, check that serve-dispatch is still running in the other session.
5. If `render_html`, run `uv run python -m wikify_simple.cli html --bundle $BUNDLE`.
6. If `run_eval`, run `uv run python -m wikify_simple.cli eval --bundle $BUNDLE --corpus {corpus}`.
7. Report the final bundle path, HTML output path, metrics summary, and the orchestrator action trajectory from `_calls.jsonl` + `policy_actions` in `_run.json`.

## Cost note
The orchestrator runs at tier L (opus, locked) and a single decision costs ~30k haiku-equivalent tokens. The LLM policy caches each active sampling action for up to 8 consecutive extract batches before re-querying — without this cache, a full iteration's orchestration overhead would exceed the extract+write budget. Control actions (`set_tier`, `set_allocation`) and `done` are never cached: they trigger an immediate re-query on the next batch.

Budget your run accordingly: expect 1 orchestrator call per ~8 extract batches, PLUS the startup call at iteration 1. For ~20-40 extracts per iteration, that's 3-6 orchestrator calls = 90-180k heq just for orchestration. Scale the per-iteration budget to at least 200k if you want non-trivial extract+write work on top.

## Outputs
Same as run-scripted: bundle, HTML, metrics. Additionally:
- `_calls.jsonl` contains every orchestrator action picked, with args and resulting sampling batches.

## Failure modes
- serve-dispatch not running → harness times out after 600s per request.
- Orchestrator returns `done` too early → bundle has few pages; re-run with more iterations.
- Budget exhausted before the orchestrator picks `done` → the cost meter aborts cleanly; whatever pages were written are on disk.
