# Wikification strategies

> **Current state & roadmap**: this document describes the "cube" parameterization of deterministic strategies (sampler × schedule × tiering) and the four anchor cells (E, M, X, agent). Several structural additions are planned in [`plans/structural-improvements.md`](plans/structural-improvements.md) and are NOT yet landed:
>
> - **LLM-as-sampler (Phase 3)**: the `llm_policy` orchestrator today picks a sampling *category* (`walk_local`, `jump_uniform`, `jump_pagerank`, `jump_gap`, plus the newly-added `set_allocation` / `set_tier` / `done` control actions). Phase 3 adds a `pick_chunks` action with direct chunk-id selection, backed by a compact `sampler_snapshot` in the orchestrator context. This turns the agent cell from "picks the sampling strategy" into "IS the sampler".
> - **Images as first-class units (Phase 4)**: caption chunks are tagged in the sampler state, get differentiated residual handling, and a new `jump_figures` action targets high-residual captions.
> - **Orchestrator call cadence**: the `llm_policy` caches active sampling actions for up to 8 consecutive extract batches before re-querying (so orchestrator cost amortizes). Control actions (`set_tier`, `set_allocation`, `done`) bypass the cache.
> - **Per-iteration bundle pinning**: the `--bundle` CLI flag pins the bundle path across `create`+`refine` iterations. Multi-iteration workflows must use it.
>
> Pricing-normalized tiers are S=1/5, M=3/15, L=15/75 (input/output haiku-equivalent per token) — see `src/wikify_simple/infra/config.py`.

## The cube

A strategy is one cell in a three-axis cube, on top of one shared
infrastructure layer. Three axes, seven knobs, one orthogonal flag.

```
              +-----------------------------------------+
              | shared infrastructure                   |
              |   cache · context envelope · cost meter |
              +-----------------------------------------+
                                |
        +-----------+-----------+-----------+
        |           |                       |
        v           v                       v
     axis A       axis B                 axis C
     sampler      schedule               tiering
   (local_op,   (exploit_fraction,   (tier_explore,
    global_op,   adaptive)            tier_exploit)
    jump_rate)

   + dedup_after_extract : bool        (free flag)
```

The seven knobs collapse what was previously a list of named strategies
into an interpretable parameter space grounded in search-theory: the
two-process structure (local exploit + global jump) from Lévy-flight
foraging and frontier-based exploration, the explore/exploit split from
bandit theory, and tier-per-mode for cost-aware planning.

## Shared infrastructure

Three pieces, used by every strategy. Built once, locked, never reasoned
about again.

### Extraction cache

Per-chunk small-model extraction is deterministic in
`(chunk_text, prompt_template, model_id, decoding_params)`. It is the
largest cost item in any strategy and must be cached.

```
data/cache/extract/{model_id}/{prompt_hash}/{chunk_id}.json
```

Properties:

- **Free re-runs.** Same `(corpus, prompt)` → zero extraction tokens after
  the first time. Ablation cells share most of their reads with their
  anchor cell, so the marginal cost of an ablation is far below its
  notional cost.
- **Cross-strategy fairness.** Two samplers that read the same chunk pay
  for it once.
- **Honest telemetry.** The cache stores the *first-time* token cost; the
  cost meter reports it whether the call hit or missed, so the study
  measures real compute.
- **Reproducibility.** Same corpus + same cache + same seed = same bundle.

### Context envelope

A single global context cap, allocated across the slots of each call by a
priority-fill builder.

```
TOTAL_CONTEXT     = 128K        # global cap, every call
RESPONSE_RESERVE  = 8K          # reserved for the model output
                                # → effective input cap = 120K
```

The cap is a *ceiling*, not a floor. Typical extractor calls use 3K–8K
because the spec ceilings stop the fill early. The cap only bites for
unusual calls (very long chunks, foundational pages with many evidence
items). Raising the cap raises *headroom*, not typical cost.

The envelope is built fresh for every call. Nothing accumulates between
calls. Each role has a *spec list* of slots in priority order, with
floors and ceilings:

```python
EXTRACTOR_SPEC = [
    Required("schema",            fixed=1K),
    Required("target_chunk",      variable),
    Pool("canonical_titles",      floor=1K, ceiling=4K, ranker=cosine),
]

WRITER_SPEC = [
    Required("schema",            fixed=1K),
    Required("page_skeleton",     variable),
    Pool("evidence_chunks",       floor=4K, ceiling=80K, ranker=relevance),
    Pool("neighbor_summaries",    floor=0,  ceiling=8K, ranker=g_evidence),
]

ORCHESTRATOR_SPEC = [
    Required("state_header",      fixed=2K),
    Required("action_menu",       fixed=2K),
    Pool("page_index",            floor=4K, ceiling=40K, ranker=recency),
    Pool("action_history",        floor=4K, ceiling=20K, ranker=recency),
    Pool("open_candidates",       floor=2K, ceiling=20K, ranker=score),
]
```

The builder walks the list, gives every Required its fixed share, gives
every Pool at least its floor, then distributes leftover budget top-down
by priority until 120K is hit. Items in any pool that don't fit are
summarised in one line ("23 more elided").

**Hard rule.** No model context grows with run length. Period. The
extraction cache, the eval harness, and the cost meter all rely on this
invariant.

### Cost meter

One `CostMeter` per run, threaded through every model call. Records per
call: role, tier, input_tokens, output_tokens, context_used, context_cap,
wall_seconds, cache_hit, prompt_hash. Aggregates into the run's
`_run.json` and a per-call `_calls.jsonl` event log.

Three guarantees:

1. **Hard abort on budget overrun** at `1.05 × budget_target`.
2. **Hard abort on context overrun** if a builder ever produces a prompt
   over the cap (asserts a builder bug).
3. **No silent zero-tokens.** A response with `usage = None` raises.

Live status to stderr every 10 calls or 5 seconds, whichever comes first.

## Axis A — Sampler

A sampler is a parameter triple. Every old A1–A8 falls out as a special
case.

| Variable | Domain | Granularity | Meaning |
|---|---|---|---|
| `local_op` | `none` / `similarity_walk` / `refine_uncertain` | chunk | What "step locally" means: walk `similar_strong`/`co_section` from the current concept's evidence chunks (`similarity_walk`), or pick chunks adjacent to high-entropy cached extractions (`refine_uncertain`). |
| `global_op` | `uniform` / `pagerank` / `coverage_gap` | doc-then-chunk *or* chunk | What "jump globally" means. |
| `jump_rate` | `[0, 1]` | – | Per step, probability of a global jump instead of a local step. The Lévy mixing parameter. |

### Granularity rules (chunk vs doc)

The corpus graph has both chunk-level edges (`similar_strong`,
`co_section`) and doc-level edges (`cites`, `doc_similar`). Each operator
lives at exactly one level:

| Operator | Level | Why |
|---|---|---|
| `similarity_walk` | chunk | walks `similar_strong` / `co_section` |
| `refine_uncertain` | chunk | uncertainty is per-chunk |
| `uniform` (global) | **doc-then-chunk** | pick a doc, then read 3 chunks (abstract + top-2 by `similar_strong` degree). Pure chunk-uniform is dominated by long documents. |
| `pagerank` (global) | **doc-then-chunk** | PageRank lives on `cites` / `doc_similar`; once a doc is picked, the same per-doc rule applies. |
| `coverage_gap` (global) | chunk | the M1 residual is per-chunk; doc-level averages destroy the gradient. |

A local step costs 1 chunk of budget. A doc-then-chunk global jump costs
`chunks_per_landed_doc = 3` chunks of budget; a chunk-native global jump
costs 1. `chunks_per_landed_doc = 3` is a locked v1 constant, not a
study knob.

### Images as first-class units

Images (figures, tables-as-images, slide images, page screenshots) are
sampled by the same machinery as text chunks. Two operating modes,
selected per strategy as a sub-flag of `local_op` / `global_op`:

- **`images = caption_only` (default).** The image's caption + alt text
  are embedded at ingest and indexed *as if they were chunks*. The
  sampler treats them as ordinary candidates for `similar_strong` walks,
  `coverage_gap`, and `pagerank`. Picking an "image chunk" feeds the
  caption text to the extractor; the image binary itself is never
  loaded by a model. Cheap, deterministic, no vision tier needed.
- **`images = vision_on_demand`.** Same as `caption_only`, but when the
  extractor sees a caption it considers "informative" (a tier-S decision
  on a one-line prompt), it can request the actual image bytes. The
  image is then included in the next extract call as a multimodal input
  at the same tier the extractor uses. The decision to "look at" the
  image is itself a budget-priced action.

The default is `caption_only` because it costs nothing extra and gets
images into the corpus graph + into M1's coverage residual. The
`vision_on_demand` mode is held in reserve for corpora where caption
text is genuinely insufficient (slide-heavy decks, figure-heavy review
articles); turn it on as a separate ablation, not as the default.

For the agent cell, the action menu gains `inspect_figure(image_id)`
which dispatches a one-shot vision call regardless of mode — the agent
has explicit control. Same hard budget rules.

### Bootstrap (round zero)

When the wiki is empty, `local_op` is undefined. The first round of any
strategy uses `global_op` only (forced `jump_rate = 1`) until the wiki
has at least one concept page with evidence. The bootstrap rule is
fixed: an abstract sweep — one chunk per doc (the abstract chunk) — for
the smallest possible scaffold to anchor subsequent walks.

## Axis B — Schedule

| Variable | Domain | Meaning |
|---|---|---|
| `exploit_fraction` | `[0, 1]` | Fraction of total budget spent on `write` (exploit) vs `extract` (explore). Curate gets a fixed small slice (~5%). |
| `adaptive` | `bool` | Static split if `false`. If `true`, the split is re-tuned mid-run from the Heaps slope `dN/dC`: when novelty drops below threshold, shift remaining budget toward write. |

## Axis C — Tiering

| Variable | Domain | Meaning |
|---|---|---|
| `tier_explore` | `S` / `M` / `L` | Tier used for the extract stage. |
| `tier_exploit` | `S` / `M` / `L` | Tier used for the write stage. Curate inherits the higher of the two. |

## Free flag — `dedup_after_extract`

Drop candidate concepts whose normalised title already exists as a wiki
page id or alias. Always-on by default; toggled only as an ablation.
Reported as a column on every run, not as a study axis.

## The four anchor cells

Three deterministic anchors plus one model-driven cell. Each is one
specific point in the seven-knob space.

| Cell | sampler                                       | schedule         | tiering | One-line interpretation |
|------|-----------------------------------------------|------------------|---------|-------------------------|
| **E** explore | `(none, pagerank, 1.0)`                  | `(0.2, static)`   | `(S, S)` | breadth-first cheap floor |
| **M** mixed   | `(similarity_walk, coverage_gap, 0.1)`   | `(0.4, adaptive)` | `(S, L)` | the Lévy + Bayesian-opt prescription; the headline candidate |
| **X** exploit | `(similarity_walk, none, 0.0)`           | `(0.6, static)`   | `(M, L)` | depth-first quality ceiling |
| **agent**     | model-driven (replaces axes A and B)     | – | `(tier_master=L, S, M)` | upper reference for state-leverage; one expensive model in a planning loop |

`E` is the cost floor; `X` is the quality ceiling; `M` is the literature-
blessed middle and the candidate the study expects to win; `agent` is the
upper reference that tells us how much the deterministic loop is leaving
on the table.

### The agent cell

The `agent` cell replaces axes A and B with one master model running a
planning loop. The model picks among a fixed action menu; the harness
executes each action and updates state.

Action menu (the only LLM verbs in the cell):

| Action | Cost | Returns |
|---|---|---|
| `walk_local(concept_id, k)` | k × tier_explore | k chunks via `similarity_walk` |
| `jump_uniform(n_docs)` | n × 3 × tier_explore | abstract + 2 chunks each |
| `jump_pagerank(n_docs, graph)` | n × 3 × tier_explore | pagerank-weighted docs |
| `jump_gap(k)` | k × tier_explore | k chunks from current `coverage_gap` set |
| `propose_concept(title, aliases, evidence_ids)` | ~free | adds candidate (no model call) |
| `merge_concepts(a, b)` | ~free | merges two candidates |
| `inspect_page(id)` | small × tier_master | returns one page in full (load-bearing: the orchestrator's only way to see a body) |
| `write_page(id)` | 1 × tier_exploit | runs the write step |
| `inspect_metric(name)` | ~free | returns a current scalar (`F`, `β`, `Q`, count) |
| `done` | – | terminates |

The orchestrator runs one tier-`L` SDK call per action, each with the
ORCHESTRATOR_SPEC envelope. The action verbs share primitives with the
deterministic cells, so cache hits across cells are real.

Hard caps: `max_actions = 4 × n_concepts_target`, mandatory `write_page`
quota by mid-budget, single-action token cap from the envelope. Susceptibility
is reinstated for this one cell — agent runs use 2 seeds.

## Per-axis ablations from M

| Ablation | Variant set | Cells |
|---|---|---|
| `jump_rate` | `{0.0, 0.1, 0.3, 1.0}` | 4 |
| `local_op` | `{none, similarity_walk, refine_uncertain}` | 3 |
| `global_op` | `{uniform, pagerank, coverage_gap}` | 3 |
| `exploit_fraction` | `{0.2, 0.4, 0.6}` | 3 |
| `adaptive` | `{false, true}` | 2 |
| `(tier_explore, tier_exploit)` | `{(S,S), (S,M), (S,L), (M,L)}` | 4 |
| `dedup_after_extract` | `{on, off}` | 2 |

Total: **21 ablation cells per corpus**, each at the `1×` budget level.
Plus the 4 anchor cells at three budgets each = **12 anchor runs**.
Grand total: **33 runs per corpus**, all coherent. Replicate on a second
corpus only if axis rankings look corpus-dependent on the first.

## Locked v1 constants

These are not study knobs. They are fixed at config time.

| Constant | Value | Where it lives |
|---|---|---|
| `TOTAL_CONTEXT` | 128K | context envelope |
| `RESPONSE_RESERVE` | 8K | context envelope |
| `chunks_per_landed_doc` | 3 | sampler |
| `dedup_after_extract` (default) | `on` | extract pipeline |
| `bootstrap_rule` | abstract sweep | sampler |
| `cost meter abort threshold` | 1.05 × budget | cost meter |
| `live status interval` | 10 calls or 5 seconds | cost meter |
| `extractor cache key` | `(model_id, prompt_hash, chunk_id)` | cache |
| `K, M, N, P` | derived from spec ceilings | context envelope |

## Open questions

1. **`jump_rate = 0.1` as the locked Lévy default**, swept only if the
   ablation row shows `M` is sensitive to it.
2. **Adaptive switch threshold**: at what value of `dN/dC` does the
   schedule shift to write-heavy? Pick by eye on the first real run.
3. **`refine_uncertain` viability**: needs a probe — does cached-extraction
   entropy correlate with anything useful? Drop the option if not.
4. **`agent` master-tier ablation**: also test `M` as master (cheaper
   planner) to see if `L` is necessary.
5. **Bootstrap budget**: how many chunks does round zero get? My lean is
   `min(n_docs, 0.1 × total_budget)` so it scales with corpus size but
   never dominates.
