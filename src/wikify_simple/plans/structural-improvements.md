# wikify_simple — Structural improvements from the test-run findings

## Context

Two test runs (3-iter scripted at 50k heq/iter + 1-iter LLM campaign at 30k heq) on the 20-paper mvp20_v6 corpus surfaced a set of structural issues that the prior restructuring did not address. These are the "why is it this way" issues, not the "what's it called" issues. The test-run report in the conversation captured 12 findings; this plan implements the structural ones the user selected and leaves the tactical/informational ones as-is.

**What this plan delivers:**

1. **Ingest-time pre-computation** of everything that is a pure function of the corpus, so distill iterations reuse it instead of rebuilding from scratch.
2. **Two specific performance fixes from the open-issues list (Issue 1):** the long-running multi-iteration campaign driver (option 4) and the guard that skips irrelevant loads when `phase == "write"` (option 6).
3. **LLM-as-sampler:** the orchestrator picks chunk ids directly via a `pick_chunks` action, with a small set of helper tools (`semantic_query`, `inspect_pages`) to feed it the context it needs without bloating every decision. Keeps today's `walk_local` / `jump_*` / `set_allocation` / `set_tier` / `done` actions available as convenience shortcuts.
4. **Images as first-class sampling units:** caption chunks get explicit tagging, differentiated residual handling, a new `jump_figures` sampling op, an image-coverage metric, and handler-level awareness so the extractor populates `evidence_figures` when captions match emitted concepts. Specifically addresses points 1–5 from the "images are sampled" gap list.
5. **Three remaining tactical fixes** called out in my earlier open-issues review: dispatch latency (smaller `POLL_INTERVAL` + per-batch parallel dispatch), writer prompt length (compress layers + hash-based layer delivery that caches within a `serve-dispatch` session), and budget overshoot (explicit write reservation + per-write pre-check). **Vendor-neutral by design — no Anthropic/OpenAI SDK dependency is introduced.** The hash-based layer cache is implemented in the skill runtime, not via vendor prompt-caching primitives (though it stays compatible with them for a future opt-in).
6. **Output quality fixes from rendered-HTML review:** stricter article section structure (validator + handler), people pages written by models (delete deterministic `build_author_pages`, author metadata becomes writer context), HTML renderer + index filter skeletons and fix broken lists, rename `concepts` → `articles` in kind / directory / UI, and artifact templates rewritten against the Wikipedia Manual of Style. All five grounded in concrete issues observed in the actual rendered output of the scripted and campaign test runs.

---

## Phase 1 — Ingest pre-compute + corpus-level artifacts

### Problem

`distill/pipeline.py::_build_sampler_state` (pipeline.py:567-606) rebuilds pure corpus-only structures on every run: `chunks_by_doc`, `chunk_to_doc`, `abstract_chunk_by_doc`, `neighbors_by_chunk`, `chunk_degree`, `pagerank_doc` (currently uniform via `_uniform_pagerank` at pipeline.py:619), and the initial coverage heap.

For mvp20 this is ~1-2 s of setup; at 60k chunks it would be 20-40 s × number of iterations × number of parallel sweep cells.

### Design

Ingest produces a new `corpus/sampler_index.json` file that contains everything pure. Distill loads it and skips the rebuild. The adjacency index can be very large, so we use msgpack for the sampler index (optional dependency; fall back to JSON if not present) or keep JSON but chunk the large dicts — decide during implementation.

Additionally, ingest will also write `corpus/pagerank.json` — a real PageRank on the doc graph built from `cites + doc_similar` edges — so the `pagerank_doc` field is no longer uniform. NetworkX is already imported elsewhere (`store/corpus_profile.py`) so we reuse that.

### Files to modify

- `src/wikify_simple/paths.py` — add `sampler_index_path` and `pagerank_path` properties on `CorpusPaths`
- `src/wikify_simple/ingest/refresh.py` — after building the corpus graph (existing line ~114), compute the pure sampler-state fields + real pagerank, serialize both
- `src/wikify_simple/ingest/sampler_index.py` **NEW** — `build_sampler_index(docs, chunks, graph, vectors) -> dict` + `save_sampler_index(path, index)` + `load_sampler_index(path) -> dict | None`. The build function is extracted verbatim from `_build_sampler_state` so there's a single source of truth.
- `src/wikify_simple/distill/pipeline.py::_build_sampler_state` — call `load_sampler_index(corpus.sampler_index_path)`; if present, assemble `SamplerState` directly from it; if missing (older corpus), fall back to the existing in-memory build and warn on stderr.
- `src/wikify_simple/distill/pipeline.py::run` — for `phase == "write"`, skip the `read_vector_store`, `read_graph`, and sampler-state build entirely. The write phase only needs docs, chunks, images_index, and the write request manifest.
- `tests/wikify_simple/test_sampler_index.py` **NEW** — round-trip build/save/load returns the same `SamplerState` fields as today's `_build_sampler_state`.

### Shape of `corpus/sampler_index.json`

```json
{
  "version": 1,
  "chunks_by_doc": {"<doc_id>": ["<chunk_id>", ...], ...},
  "chunk_to_doc": {"<chunk_id>": "<doc_id>", ...},
  "abstract_chunk_by_doc": {"<doc_id>": "<chunk_id>", ...},
  "neighbors_by_chunk": {"<chunk_id>": ["<chunk_id>", ...], ...},
  "chunk_degree": {"<chunk_id>": int, ...},
  "caption_chunk_ids": ["<chunk_id>", ...],
  "content_chunk_ids": ["<chunk_id>", ...],
  "doc_ids_sorted": ["<doc_id>", ...]
}
```

Note `caption_chunk_ids` — this is what Phase 4 keys off of.

### Verification

- `uv run python -m wikify_simple.cli ingest data/papers/mvp20 --out data/wikify_simple/corpora/mvp20_next` produces `sampler_index.json` + `pagerank.json`.
- `diff` the resulting `SamplerState` fields before/after the refactor on a fixture corpus — zero difference.
- `uv run pytest tests/wikify_simple/test_sampler_index.py` passes.
- `uv run pytest tests/wikify_simple/test_pipeline_reallocate.py` still passes (exercises pipeline.run on a tiny corpus).
- Wall time on the mvp20 corpus startup (instrument with a `time.monotonic()` around `_build_sampler_state`) drops from ~1 s to <50 ms.

---

## Phase 2 — Long-running multi-iteration campaign driver (Issue 1 option 4)

### Problem

Each iteration is a fresh Python process. Every iter re-loads chunks, vectors, graph, and rebuilds the sampler index. The ExtractCache is the only thing that survives, and it's disk-bound.

### Design

Add a new CLI verb `campaign` that accepts the same per-iteration flags as `distill` plus `--iterations N`. Inside, it loads the corpus once and runs N iterations against the same in-memory `SamplerState`, `PolicyRuntime`, `ExtractCache` handle, and `CostMeter` per iteration (the meter resets between iterations but retains the reference to the same `.jsonl` path).

Between iterations it:
1. Writes the iteration's bundle and `_run.json` snapshot.
2. Calls `save_coverage_memory(bundle, state)` so the on-disk state is coherent even if the process dies mid-campaign.
3. Re-uses the loaded corpus + sampler index.
4. Advances the RNG seed deterministically (`seed + i - 1`).
5. For `i > 1`, loads existing pages from the bundle via `load_existing_pages` and passes `iteration="refine"`.

### Files to modify

- `src/wikify_simple/cli.py` — new `campaign` command that wraps the existing `distill` body in a loop. The distill body is refactored into a helper `_run_one_iteration(...)` that takes pre-loaded state.
- `src/wikify_simple/distill/pipeline.py` — extract a new `PreloadedCorpus` dataclass (`{docs, chunks_by_id, images_index, sampler_index, vectors, graph, ...}`) and a new top-level `run_with_preloaded(preloaded, bundle, strategy, ...)` that `pipeline.run` delegates to after loading. The CLI campaign verb calls `preload_corpus(corpus)` once and `run_with_preloaded` N times.
- `src/wikify_simple/distill/preload.py` **NEW** — `preload_corpus(corpus: CorpusPaths) -> PreloadedCorpus`. Uses the sampler index from Phase 1 if present.
- `.claude/skills/wikify_simple/workflows/run-scripted.md` — point at `wikify-simple campaign` instead of looping distill calls.
- `.claude/skills/wikify_simple/workflows/run-campaign.md` — same.
- `tests/wikify_simple/test_campaign_driver.py` **NEW** — fixture corpus + 2-iteration campaign + assert the corpus load happens exactly once.

### Verification

- `uv run python -m wikify_simple.cli campaign --strategy M --iterations 3 --budget 50000 --corpus data/wikify_simple/corpora/mvp20_v6 --bundle data/wikify_simple/test_runs/campaign_next` runs three iterations in one Python process.
- Instrument corpus-load wall time: should be paid once, not three times.
- ExtractCache hit counter (`cache.hits`) is preserved across iterations (in-memory, so cache lookups within the same process are free after the first).
- `tests/wikify_simple/test_campaign_driver.py` passes.
- Full pytest still passes.

---

## Phase 3 — LLM-as-sampler (option D)

### Problem

Today the LLM picks a *category* of sampling (`walk_local`, `jump_uniform`, etc.) and the deterministic sampler picks the actual chunks. The LLM cannot target a specific chunk, a specific page to deepen, or a semantic theme.

### Design

Add three new actions to the `OrchAction` menu, keep all existing ones:

| Action | Args | Effect |
|---|---|---|
| `pick_chunks` (new) | `{chunk_ids: list[str], reason: str}` | Bypass the deterministic sampler; dispatch exactly these chunks. The policy filters out chunks already in `state.seen_chunks` and logs the reason for telemetry. |
| `semantic_query` (new tool) | `{query: str, k: int, scope: "all"|"unseen"|"page:<id>"}` | Returns a list of `{chunk_id, doc_id, short_title, preview, score, is_caption}` via cosine similarity against the corpus vector store. **This is a tool call, not a sampling action** — the orchestrator uses it to decide which chunks to `pick_chunks`. |
| `inspect_pages` (new tool) | `{page_ids: list[str] | null}` | Returns compact page summaries for the named pages (or all pages if `null`): `{id, title, n_evidence, last_drafted_run, doc_ids_count, has_body}`. Again a tool, not a sampling action. |

The tools are served by the same dispatch binding but under a new `data/dispatch/tools/` role, or — simpler — inside the orchestrator handler itself via a nested subagent that calls a local Python helper. We'll use the second approach: the orchestrator handler loads the sampler state snapshot once per decision and exposes `semantic_query` and `inspect_pages` as in-handler functions the subagent calls before it emits the final `OrchAction`.

This lets the LLM do a multi-step reasoning loop *within a single orchestrator dispatch*: query → inspect → pick_chunks. The Python pipeline sees only the final `pick_chunks` action. Cost stays bounded (1 orchestrator dispatch per 8-batch epoch, same as today's cache) but the LLM now gets real semantic steering.

The existing `walk_local` / `jump_uniform` / `jump_pagerank` / `jump_gap` stay as cheap shortcuts for early-run breadth, and the LLM can still return them without any tool calls.

### Sampler state snapshot for the orchestrator

To keep the dispatch payload lean, the `OrchState` schema gains a small `sampler_snapshot` field containing:

- `top_gap_chunks`: top-20 chunks by coverage residual, with `{chunk_id, doc_id, section_path_top, residual, is_caption}`
- `doc_coverage`: `{doc_id: n_chunks_seen}` for docs with any reads
- `page_index`: compact list of `{id, kind, n_evidence, has_body}` for up to top-50 pages by evidence count
- `content_stats`: `{n_content_chunks, n_caption_chunks, n_seen, n_pages, n_with_body}`

These are cheap to produce (already in `SamplerState`) and give the LLM enough global view to pick well. Total ~2-4 kB of JSON, which at tier L is ~1500 heq — a modest increment on the ~30k per decision.

### Files to modify

- `src/wikify_simple/contracts/schema.py` — extend `OrchState` with `sampler_snapshot: dict = Field(default_factory=dict)`; no schema change to `OrchAction`
- `src/wikify_simple/distill/policy.py::LlmPolicy`:
  - Build `sampler_snapshot` in `next_extract` before the orchestrator call
  - New branch in `_execute_orch_action` for `pick_chunks`: dedupe against `seen_chunks`, return as `ExtractDecision(batch=...)`
  - `policy_events` records the `reason` string for every `pick_chunks` action
  - The `persist_batches` cache is bypassed for `pick_chunks` (the LLM is already making a targeted call; no reason to repeat it)
- `src/wikify_simple/distill/sampler.py` — new `semantic_query_chunks(state, query_vec, k, scope)` helper that does the cosine scan; callable from the handler via Python import
- `.claude/skills/wikify_simple/handlers/orchestrate.md` — rewrite. Document the new `pick_chunks` action, document the two tools (`semantic_query`, `inspect_pages`), show a worked example of query → inspect → pick. Keep the existing sampler actions as a "cheap fallback" section.
- `.claude/skills/wikify_simple/reference/orchestrator.md` — update the action catalog.
- `tests/wikify_simple/test_guided_control_actions.py` — add tests for `pick_chunks` (valid ids, filtered-vs-seen, empty ids) and for the sampler_snapshot construction.

### Verification

- New unit tests cover `pick_chunks` (good ids, duplicates, all-seen edge case).
- Run the LLM campaign at 50k heq/iter with the new actions available. Expected: the orchestrator uses `pick_chunks` at least once, the policy_events log records the reason, and the resulting pages are better grounded (visible by spot-checking a concept page whose evidence was picked via `pick_chunks`).
- Cost per orchestrator decision does not exceed 1.2× the current ~30 k (headroom for the snapshot payload).

---

## Phase 4 — Images as first-class sampling units

### Problem (5 specific gaps from the conversation)

1. `SamplerState` does not tag image-caption chunks, so the sampler cannot differentiate them from text chunks.
2. No sampler op to actively seek figures.
3. No "image coverage" metric distinct from text coverage.
4. Handler skills don't know to use `images_for_doc` / `evidence_figures` — figures only reach the model passively.
5. `vision_on_demand` is in the design doc but never implemented.

### Design

#### Gap 1 — tag captions in the state

- `SamplerState` gets `caption_chunk_ids: set[str]` (populated from the sampler index's `caption_chunk_ids` list built in Phase 1).
- `init_coverage_state` seeds caption chunks with a different `default_residual` — default `0.8` vs text `1.0`, configurable — so the coverage heap naturally prefers text chunks early and surfaces captions later.
- `apply_coverage_feedback` gets a `caption_near_floor = 0.4` threshold so reading a text chunk discounts its neighbor captions less aggressively than text-to-text.

#### Gap 2 — new sampler op `jump_figures`

A new `GlobalOp.FIGURES` that pops the highest-residual caption chunk from a dedicated caption heap. Sits alongside the existing `_global_coverage_gap` and is selectable via `jump_figures {k}` in the orchestrator action menu.

- `sampler.py::SamplerState` — new `caption_heap: list[tuple[float, int, str]]` and `caption_versions: dict[str, int]`
- `sampler.py::init_coverage_state` — initialize both heaps; tag caption chunks
- `sampler.py::_global_figures(state, k_per_doc)` — pop from `caption_heap`
- `sampler.py::_GLOBAL_DISPATCH` — add `GlobalOp.FIGURES` entry
- `policy.py::_execute_orch_action` — new match arm `jump_figures`
- `contracts/schema.py::OrchAction` — document the new action (only as prose; schema is `{name, args}` and doesn't need changes)

#### Gap 3 — image coverage metric

Add an `M1_image` metric as a sibling to `M1_coverage_residual`:

```
M1_image = mean_over_captions( 1 - max_over_pages( cos(embed(caption), embed(page_body)) ) )
```

- `eval/metrics.py::image_coverage_residual(bundle, caption_embeddings, embed)` — new pure function
- `eval/metrics.py` — also add `n_figures_referenced_in_bodies` count (parse `![Figure N]` from each page body) and `figure_reference_rate = n_referenced / n_total_captions`
- `cli.py::eval_bundle` — include both new fields in `_metrics.json`

#### Gap 4 — handler awareness

- `.claude/skills/wikify_simple/handlers/extract.md` — add an "Image awareness" section: if `images_for_doc` is non-empty AND a caption matches an emitted concept's title/aliases (token overlap or cosine), populate `evidence_figures: [<image_id>]` on that concept. The schema already has this field; nothing uses it today.
- `.claude/skills/wikify_simple/handlers/write.md` — already instructs the writer to mention figures; add a one-line rule: prefer figures whose ID appears in `evidence[].evidence_figures` (which the writer receives via `WriteEvidenceRefV2`).
- `contracts/schema.py::WriteEvidenceRefV2` already has evidence metadata but not figure ids — add `evidence_figures: list[str] = Field(default_factory=list)` so the writer sees which figures the extractor flagged per evidence chunk
- `distill/write/requests.py::build_write_request` — propagate `concept.evidence_figures` into the `WriteEvidenceRefV2.evidence_figures` field when building the request

#### Gap 5 — vision on demand (skeleton, not full implementation)

Full multimodal is out of scope for this phase. What we ship:

- Document in `handlers/extract.md` and `reference/orchestrator.md` that `vision_on_demand` is *available as a design* but gated behind a future binding change. No code change today.
- Add a skip-with-reason slot: the extract handler may emit `evidence_figures: []` with a `needs_vision: true` flag in `extra` (already allowed by the schema). The pipeline logs these into `_run.json::vision_requests` so later work can pick them up. This is ~10 lines in the pipeline and makes the next real vision-on-demand implementation trivial.

### Files to modify

- `src/wikify_simple/ingest/sampler_index.py` **NEW** (shared with Phase 1) — include `caption_chunk_ids` in the index
- `src/wikify_simple/distill/sampler.py` — caption heap, `_global_figures`, tagged residuals
- `src/wikify_simple/distill/policy.py` — `jump_figures` action
- `src/wikify_simple/contracts/schema.py` — `WriteEvidenceRefV2.evidence_figures`
- `src/wikify_simple/distill/write/requests.py` — propagate `evidence_figures` into write request
- `src/wikify_simple/eval/metrics.py` — `image_coverage_residual`, figure-reference counts
- `src/wikify_simple/cli.py::eval_bundle` — emit the new fields
- `.claude/skills/wikify_simple/handlers/extract.md` — image awareness
- `.claude/skills/wikify_simple/handlers/write.md` — prefer evidence-flagged figures
- `.claude/skills/wikify_simple/reference/orchestrator.md` — document `jump_figures`
- `tests/wikify_simple/test_image_sampling.py` **NEW** — captions tagged, `jump_figures` picks from caption heap, image coverage metric returns a float
- `tests/wikify_simple/test_metrics.py` — add `image_coverage_residual` case

### Verification

- Fresh ingest of mvp20 produces a `sampler_index.json` whose `caption_chunk_ids` matches the number of images with captions (~164 for mvp20_v6).
- A distill run with strategy M executes the pipeline without error.
- Orchestrator-driven run can issue `jump_figures {k: 3}` and receive 3 caption chunks.
- `wikify-simple eval` reports non-zero `image_coverage_residual` and `figure_reference_rate`.
- Write requests carry `evidence_figures` when the extractor flagged them.
- Unit tests pass.

---

## Phase 5 — Dispatch latency, writer prompt length, budget overshoot

Three tactical fixes from the earlier open-issues list. All three are bounded in scope and none of them binds us to a vendor SDK (the in-process Anthropic binding that was floated earlier is explicitly excluded).

### 5A — Dispatch latency

Two changes, both small. The goal is to cut per-dispatch overhead in real `serve-dispatch` runs without changing the file-dispatch architecture.

**5A.1 — Tighten `POLL_INTERVAL`.**
Today `infra/config.py::POLL_INTERVAL = 0.25` adds up to 500 ms of unnecessary waiting per round-trip (both the skill's poll and the Python harness's poll). Drop to `0.05` (50 ms). The 250 ms figure was picked when the concern was file system load; modern SSDs don't care. This is a one-line edit.

**5A.2 — Parallelize within an extract batch.**

Today `pipeline.py::run` (around line 290) iterates over the batch returned from the policy and dispatches chunks one at a time, blocking on each response. Rewrite the inner loop to fire ALL chunks in the batch as parallel dispatches, then collect the responses. The `FileDispatchExtractor` already uses uuid-keyed request files, so multiple pending requests on the same role are already safe. The constraint is that `CostMeter` must be thread-safe — it already is (single-threaded within a Python process; we only need concurrent file-watching, not concurrent `meter.record()` calls, because we collect responses sequentially after firing).

Implementation sketch:
1. Extract a `FileDispatchExtractor.extract_many(requests: list[ExtractRequest]) -> list[ExtractResponse]` method that writes all request files up front and then polls for all responses in parallel. The existing single-chunk `extract(request)` delegates to `extract_many([request])[0]`.
2. `pipeline.py::run`'s inner extract loop batches the whole `decision.batch` and calls `extract_many`.
3. Cache hits still short-circuit: `extract_many` splits its input into cached and uncached, returns cached results immediately, and only dispatches the uncached ones.

For a 4-chunk batch, wall time goes from `4 × 25s = 100s` to `~25s + 1s coordination = 26s` when a `serve-dispatch` skill-loop can handle all 4 concurrently.

**Caveat**: the single conversation-session test harness I used for the test runs (one dispatch servicer spawning Task subagents sequentially) will NOT benefit — Task subagents aren't parallelizable from a single parent. A production `serve-dispatch` skill running in a long-lived Claude Code window CAN run multiple handler invocations in parallel if it's designed to. So this fix cashes out in production, not in the conversation-based test loop.

### Files to modify (5A)

- `src/wikify_simple/infra/config.py` — `POLL_INTERVAL = 0.05`
- `src/wikify_simple/bindings/file_dispatch.py` — add `extract_many`, keep `extract` as a thin wrapper
- `src/wikify_simple/distill/pipeline.py` — inner extract loop uses `extract_many`
- `src/wikify_simple/contracts/protocols.py` — `Extractor` protocol stays the same (single-request); `extract_many` is a binding-level optimization, not part of the protocol. The pipeline uses `getattr(extractor, 'extract_many', None)` and falls back to the serial path if unavailable.
- `.claude/skills/wikify_simple/runtime/serve-dispatch.md` — note that the runtime skill should handle concurrent requests (poll the whole dispatch dir, spawn handlers in parallel where the hosting Claude Code session supports it)

### Verification (5A)

- New test `tests/wikify_simple/test_extract_many.py` — fake binding + 4-request batch completes in roughly the same wall-clock as 1 request.
- Existing tests still pass.
- On a mvp20 real-binding run, end-to-end wall time per iteration drops by at least 2× (rough target: current 277 s → ~120 s at the 10-extract + 2-write scale).

---

### 5B — Writer prompt length (no SDK lock-in)

Today every `WriteRequest` carries `corpus_persona` (~500 tokens), `style_guide` (~1250 tokens), `field_guide` (~400 tokens), `artifact_template` (~500 tokens), plus evidence — the fixed prefix is ~2600 tokens × 3× (tier M input rate) = ~7800 heq on *every* write call before a single evidence token is paid for.

Three cumulative fixes:

**5B.1 — Compress the stable layers.**

Pure content edit, no code change. Audit `prompts/style_guide.md`, `prompts/fields/materials_science.md`, and `prompts/artifact_types/wiki_concept.md` and rewrite each as a terse rule list rather than a prose lecture. Target: `style_guide` ≤ 400 tokens, field guide ≤ 150 tokens, artifact template ≤ 200 tokens. This alone drops per-write input from ~2600 to ~1250 tokens, halving the fixed prompt cost (~3900 heq savings per write).

**5B.2 — Layer cache key + vendor-neutral delivery.**

Replace "re-send everything per request" with "send a compact reference per request, full layer only when it changes."

- At the top of each run, the pipeline writes `<bundle>/_meta/prompt_layers/<layer_hash>.md` for each unique layer (style, field, artifact, persona). The hash is content-based.
- `WriteRequest` carries `style_guide_hash`, `field_guide_hash`, `artifact_template_hash`, `corpus_persona_hash` instead of the full strings (plus the full strings as fallback, for the fake/heuristic bindings that don't load from disk).
- The `serve-dispatch` runtime skill maintains an in-memory cache of `{hash: text}`. On each write dispatch, it reads the hashes, fetches any uncached layers from `_meta/prompt_layers/<hash>.md`, composes the full prompt locally, and passes it to the Task subagent. Subsequent writes in the same serve-dispatch session reuse the cached text — **the layer text is sent to the model exactly once per serve-dispatch lifetime**, not per write.

No Anthropic SDK needed — the caching lives in the skill runtime (a plain Claude Code session's memory), not in vendor prompt-caching primitives. If we later want to plug in Anthropic's native prompt caching, this design already provides stable hashes for `cache_control` markers; if we want OpenAI/other, the exact same hashes work. **Vendor-neutral by design.**

- Net effect: the fixed prompt tokens are paid ~once per serve-dispatch session instead of once per write. For a 10-write iteration that's a ~10× savings on fixed-prompt cost.

**5B.3 — Stop sending neighbor summaries twice.**

`WriteRequest.neighbor_titles` and `WriteRequest.neighbor_summaries` both convey page neighborhood info (see `contracts/schema.py` write request fields). Keep one (summaries) and drop the other. Small, measurable win.

### Files to modify (5B)

- `src/wikify_simple/prompts/style_guide.md` + `prompts/fields/*.md` + `prompts/artifact_types/*.md` — compress
- `src/wikify_simple/prompts/registry.py` — new `compose_writer_prompt_layer_hashes(field, artifact) -> dict[str, str]` that returns content hashes alongside the existing string loader
- `src/wikify_simple/contracts/schema.py::WriteRequest` — add `{style_guide_hash, field_guide_hash, artifact_template_hash, corpus_persona_hash}` optional fields; keep the string fields for the fake/heuristic bindings
- `src/wikify_simple/distill/write/requests.py::build_write_request` — populate both the strings and the hashes
- `src/wikify_simple/distill/pipeline.py::run` — write `<bundle>/_meta/prompt_layers/<hash>.md` at startup for each distinct layer
- `src/wikify_simple/paths.py::BundlePaths` — add `prompt_layers_dir` property
- `.claude/skills/wikify_simple/handlers/write.md` — document the hash-based layer fetch; instruct the handler to cache `{hash: text}` between calls; fall back to the inline strings if hashes aren't present
- `.claude/skills/wikify_simple/runtime/serve-dispatch.md` — note that the runtime should keep a session-scoped prompt-layer cache and pass it into write handler invocations

### Verification (5B)

- On mvp20, a campaign with 3 iterations × 5 writes each: fixed-prompt cost paid once, not 15 times.
- `_run.json` shows the per-write `tokens_in` drops from ~2500 to ~1200 (just evidence + brief), a ~50% reduction in writer input tokens.
- Unit test: `test_writer_prompt_layers.py` asserts that two sequential write requests in the same process carry the same layer hashes and that `_meta/prompt_layers/` contains the expected files.
- Existing `test_write_validator.py` still passes.

---

### 5C — Budget overshoot (writer reservation)

Writes cost ~30 k heq each at tier M. On a 50 k-heq iteration budget, a greedy extract loop can burn 49 k before the write phase even starts, and the first write pushes to 79 k = 158 % of budget. The cost meter aborts, but only AFTER the overshoot.

The fix is a pre-reservation: before the extract loop starts, compute how many writes we want to afford in this iteration, reserve that budget, and forbid the extract loop from dipping into it. If the schedule says `exploit_fraction = 0.65`, that is already the intent; the bug is that it's not enforced as a hard ceiling on extract spend.

### Design (5C)

Two changes:

**5C.1 — Hard cap on extract spend.**

`pipeline.py::run` already computes `split = strategy.schedule.initial_split(budget_haiku_eq)` (line ~258 area). The extract loop's `while` condition is `meter.spent_haiku_eq < split.extract_haiku_eq`. The issue is that `split.extract_haiku_eq` is the *ideal* share, not a firm floor/ceiling. When the budget gets re-split mid-loop (due to an LLM `set_allocation` action, or the adaptive schedule), the check stays consistent but doesn't protect a reserve for the write phase.

Change: the extract loop always checks against `min(split.extract_haiku_eq, budget_haiku_eq - expected_write_reserve)` where `expected_write_reserve` is initialized at the top of `run` as `split.write_haiku_eq * 0.95` (the 5 % slack lets one extract call overshoot without killing the reserve). Once the extract loop exits, the write loop has at least `expected_write_reserve` worth of headroom.

**5C.2 — Per-write pre-check.**

Before each write call, check `meter.spent_haiku_eq + avg_write_cost > budget_haiku_eq * 1.05` and break the write loop early with a recorded reason. `avg_write_cost` starts as a conservative ~30 k heq and is updated after each real write call to the running mean. This makes budget overshoot controlled: we write as many pages as the reserve supports and stop cleanly instead of tripping the 1.05× hard abort.

The `write_rejections` list already gets populated for validator failures (from the earlier fix); extend it with a `budget_truncated` entry for pages that were skipped due to reserve depletion.

### Files to modify (5C)

- `src/wikify_simple/distill/pipeline.py` — compute `expected_write_reserve`, tighten extract loop condition, pre-check before each write call, record `budget_truncated` entries
- `tests/wikify_simple/test_budget_reservation.py` **NEW** — fake binding + a budget scenario + assert the final spend never exceeds `1.05 × budget_target` and that write reservation is honored
- `tests/wikify_simple/test_pipeline_reallocate.py` — may need an update if the existing assertion depends on the old (leaky) behavior

### Verification (5C)

- Re-run the scripted test (3 iter × 50 k heq) with the fix: `_run.json::budget_used_haiku_eq` stays ≤ 52 500 (i.e. ≤ 105 %) on every iteration.
- The `write_rejections` array contains `budget_truncated` entries for any pages that were skipped.
- New unit test passes.
- Existing tests pass.

---

## Phase 6 — Output quality fixes (from rendered-HTML review)

### Context

After Phases 1-5 were planned, a review of the actual rendered HTML from the test runs surfaced issues that none of the previous phases touch. These are live bugs in today's pipeline output, independent of the structural improvements above. All four sub-phases are grounded in concrete observations of the scripted + campaign bundles:

- **Memristor article** (`_html/concepts/Memristor.html`) has well-written prose and valid evidence, but **no section headings in the body** — only `## References` at the end. It reads like a single long blob, nothing Wikipedia-like.
- **Bhaswar Chakrabarti** people page (`_html/people/Bhaswar_Chakrabarti.html`) describes the person in terms of "this corpus" ("appears in this corpus only through citations...") instead of actually talking about the person.
- **Chia-Yu Chang** people page has a broken bullet list under "Publications in this corpus": items run together as prose, no hyperlinks, and a stray "1." appears because the markdown list is being rendered in a context without blank-line separation.
- **Campaign bundle**: all concept pages except the one written are empty shells but still appear in the HTML site and the index.
- The index exposes skeleton pages as first-class entries.
- The terminology "concepts" is not Wikipedia-native — the user wants "articles".

### Root causes identified by code inspection

1. `contracts/schema.py::_check_wikipedia_structure` (lines 318-376) requires `>= 1 H2 heading`, which is satisfied by `## References` alone. The body between the H1 title and the References block does not need any section headings today. The writer therefore produces one long prose block and passes validation.
2. `distill/write/author_pages.py::build_author_pages` + `_render_body` (lines 184-249) generates deterministic people page bodies via string concatenation: `"**{name}** appears in this corpus only through..."` (line 258-259), then builds bullet lists by appending raw `- ...` lines joined by `\n`. These bullet lines frequently aren't preceded by a blank line so python-markdown runs them together in the rendered HTML, and the phrasing is the exact "meta-comment about the corpus" the user flagged.
3. `render/html/render.py::build_site` renders every page it finds under `concepts/` and `people/` whether or not the body is empty. `store/wiki_index.py::build_index` likewise indexes skeletons.
4. The `kind: "concept"` literal and `concepts/` directory name are used throughout `models.py`, `contracts/schema.py`, `paths.py`, `store/wiki_files.py`, `store/wiki_index.py`, `render/html/render.py`, and 20+ tests.

### 6A — Stricter article body structure (validator + handler)

Require article pages to have **at least two `## H2` sections BEFORE the References block**, not just one H2 (which `## References` trivially satisfies today). Recommended minimum sections are listed in the handler guidance but the validator enforces only the count and placement so the writer still has freedom over naming and ordering.

- `contracts/schema.py::_check_wikipedia_structure` — add a count of non-References H2 headings; reject if `< 2`. Keep the existing paragraph-count check. Keep the "sections are guidance" principle — we're enforcing count and placement, not specific labels.
- The check is conditional on `page_kind == "article"` (the renamed `concept`). People pages get their own shape (see 6B + 6D).
- `.claude/skills/wikify_simple/handlers/write.md` — update the "recommended sections" block to explicitly call out the new hard minimum: "At least two `## H2` sections must precede `## References`. Use sections like `## Definition`, `## Background`, `## Mechanism`, `## Applications`, `## Open Questions`, `## Significance`, etc. Choose what fits the evidence."
- `tests/wikify_simple/test_write_validator.py` — add case: body with H2 only being `## References` is rejected; body with `## Background` + `## Mechanism` + `## References` is accepted.

### 6B — People pages written by models (delete deterministic author pages)

**Delete** `distill/write/author_pages.py::build_author_pages` as a page generator. Keep the underlying bucket-building logic but rewrite it to emit *context dicts*, not `WikiPage` objects. The context is attached to the writer's `WriteRequest` so the writer has real facts to work with, but the writer is always the one producing prose. Robust to missing metadata: when a person is extracted from the text but has no author-side info (non-author mentions), the writer has only the evidence chunks to work with.

- **Rename**: `distill/write/author_pages.py` → `distill/write/author_context.py`
- **New function**: `build_author_context(docs) -> dict[str, AuthorContext]`
  - `AuthorContext` dataclass: `{primary_publications: list[Publication], cited_works: list[CitedWork], collaborators: list[str], year_range: tuple[int, int] | None, affiliations: list[str]}`
  - `primary_publications` and `cited_works` are plain data lists; no prose, no bullet rendering, no wikilink emission.
  - Keyed by `_author_key(name)` like the existing code.
- **Delete**: `_render_body`, `_lead_paragraph`, `_notable_contributions`, `_publications_section`, `_existing_paper_links` — all the prose-generation helpers. The model does this now.
- **Pipeline**: `distill/pipeline.py::_finalize_pages` removes `pages.extend(author_pages)`. Person candidates come from the extractor (which already emits `kind="person"` entries when people are named in chunk prose — see the extract handler rules). Canonicalize merges duplicate persons by normalized name.
- **Write request construction**: `distill/write/requests.py::build_write_request` looks up the page's title in the corpus-wide author context dict. If found, attaches `author_context={"primary_publications": [...], "cited_works": [...], "collaborators": [...], ...}` to the `WriteRequest`. If not found (person mentioned in text but not a corpus author), `author_context=None`.
- **Schema**: `contracts/schema.py::WriteRequest` — new field `author_context: dict | None = None`. Document in the schema docstring that this is context-only, never emitted to disk, and may be absent.
- **Write handler skill**: `.claude/skills/wikify_simple/handlers/write.md` — add a "Writing a person page (`kind=person`)" section. Key rules:
  - Write a biographical article in Wikipedia voice. Start with the person's full name in **bold**, followed by a role/field descriptor drawn from the evidence (and `author_context.primary_publications` titles if present).
  - **Do not** start the article with "X appears in this corpus...". That phrasing is banned.
  - Structure: `## Biography` (or `## Background`), `## Contributions` (or `## Research`), optionally `## Collaborations`, `## Notable works`, `## References`. Require at least 2 H2 before References, same as articles.
  - When `author_context.primary_publications` is present, use it as grounded facts but cite each via the same chunk-level evidence that accompanies the request (not via the `author_context` field — that's not citable).
  - When `author_context` is `None`, write only from the evidence chunks. Do not speculate.
  - Publications and collaborators may be mentioned in prose. Do not produce raw bullet lists with `- ` followed immediately by prose — if you use a list, ensure a blank line separates it from surrounding prose.
- **Tests**: `tests/wikify_simple/test_author_pages.py` → mostly delete; replace with `tests/wikify_simple/test_author_context.py` that exercises `build_author_context` (publication buckets, coauthor detection, missing-metadata robustness). Add a test asserting `WriteRequest.author_context` is populated correctly for a person page and `None` for an article.

### 6C — HTML renderer + index filter skeletons, fix lists

- **Skeleton filter**: `render/html/render.py::build_site` — skip any page whose `body_markdown` is empty or below a minimum length (200 chars). Log the count of skipped pages.
- **Index filter**: `store/wiki_index.py::build_index` — same filter. The wiki index only enumerates real pages.
- **Bullet list hardening**: author-page bullet lists rendered in the old code appear as run-on text because python-markdown treats `- item` as a list only when preceded by a blank line. With 6B deleting the deterministic generator this is mostly moot, but as a safety net:
  - `render/html/render.py::_MD_EXTENSIONS` — add `"sane_lists"` (from python-markdown's built-ins) so list parsing is tolerant of inline `-` without a preceding blank.
  - Author-context-derived publications in the write handler output must use real blank-line-separated list syntax; the handler skill documents this.
- **Wikilink resolution for publication titles**: 6B moves this to the writer, which uses inline prose (no forced wikilinks). The renderer's existing `[[wikilink]]` resolution stays as the fallback for any explicit links. No new code needed.
- **Tests**:
  - `tests/wikify_simple/test_html_render.py` — add a test that a bundle containing 3 pages (2 with bodies, 1 skeleton) produces only 2 HTML output files.
  - `tests/wikify_simple/test_wiki_index.py` — add a test that skeleton pages are excluded from the index.

### 6E — Artifact templates rewritten to match real Wikipedia structure

The current `prompts/artifact_types/wiki_concept.md` and `wiki_person.md` describe an *approximation* of Wikipedia voice but do not reflect the canonical structure Wikipedia enforces via its Manual of Style. In particular, `wiki_person.md` still describes a "two-tier" structure where the deterministic author-page skeleton is preserved by the writer — that whole model goes away in 6B, so the template needs a full rewrite. And `wiki_concept.md`'s "sections are guidance, not strict" framing has proven too permissive: the Memristor page passed validation with zero in-body H2 headings.

Both templates are rewritten against the authoritative Wikipedia Manual of Style, fetched at plan time:
- https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Layout
- https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Biography

#### Canonical Wikipedia article layout (from MoS/Layout)

1. **Lead section** (no heading): bold title in the first sentence, one-sentence definition of what the subject IS, then context expanding on significance. No bullet points in the lead. The lead is a self-contained summary of the article.
2. **Body sections** at `## H2`, in topically-ordered hierarchy. Consecutive, no skipped levels. Single blank line between sections.
3. **Standard appendices in this exact order**:
   1. **Works** or **Publications** (biographies only)
   2. **See also** (optional)
   3. **Notes and References** (always)
   4. **Further reading** (optional)
   5. **External links** (optional)
4. Avoid oversectioning (many trivially-short sections) and undersectioning (one long blob — the current Memristor failure mode).

#### Canonical Wikipedia biography layout (from MoS/Biography)

1. **Lead sentence pattern**: `**Full Name** (birth–death) was a [nationality] [occupation] known for [primary achievement].` For a corpus-driven biography, the dates are usually just the year range of publications in the corpus and nationality is often unknown — the template must degrade gracefully.
2. **Section order** (use only those the evidence supports):
   1. **Early life** / **Education** — typically unavailable from a research-paper corpus; omit if no evidence.
   2. **Career** / **Research** / **Professional life** — major roles, contributions, timeline. Academic biographies integrate achievements chronologically here.
   3. **Research contributions** / **Key findings** — primary payload for a scientific biography: what they discovered, methods developed.
   4. **Personal life** — almost always omitted for scientific biographies.
   5. **Legacy** / **Impact** — when evidence supports it.
   6. **Awards and honors** — rarely available from paper metadata; omit without evidence.
   7. **Selected works** / **Publications** — replaces the current meta-phrased "Publications in this corpus" bullet list.
   8. **References** (always).
3. **Writing rules**:
   - Neutral factual tone. Banned puffery: "legendary", "brilliant", "pioneering", "seminal", "groundbreaking", "renowned".
   - No honorifics (Dr., Prof.) except Wikipedia-recognised ones (Sir, Dame).
   - Past tense for biographical facts unless the person is known to be active and affiliations are current.
   - **Do not describe the subject in terms of "this corpus" or "this wiki".** The person exists independently of the corpus; the corpus is the evidence source, not the topic.
   - **Banned phrases, explicitly**: "appears in this corpus", "mentioned in this corpus only through citations", "in this corpus", "this corpus contains". These are the symptoms the Bhaswar Chakrabarti page exhibited.

#### Rewrite plan

Both templates are rewritten from scratch.

**`wiki_concept.md`** (renamed to `wiki_article.md` as part of 6D):
- Lead-paragraph section: explicit pattern with bold title, one-sentence IS definition, context sentence. Worked example included.
- **Required sections for article pages**: minimum of 2 topical H2 sections before the appendix group. Template suggests the common set (`## Background`, `## Mechanism` / `## Process` / `## Theory`, `## Applications` / `## Uses`) but explicitly allows topic-specific substitutions (`## Specifications`, `## Characterization`, `## Types`, `## Variants`, etc.) — the rule is "at least 2 H2 sections that together explain the subject", not "these specific labels".
- Appendix order: `## See also` (optional) → `## References` (required last).
- Drop the "six-section layout" framing from the old template and the loose "sections are guidance" escape hatch. 6A makes "at least 2 topical H2 before References" an enforced validator rule.
- Explicit banned-phrases list: no "in this corpus", no "in this article", no "as discussed above", no first-person references to the work.

**`wiki_person.md`**:
- **Completely rewrite** — the two-tier deterministic/model-enriched split is obsolete once 6B deletes `build_author_pages` as a page generator.
- Lead-sentence pattern following Wikipedia biography convention: `**Full Name** (year-range) is a [field] [role] known for [contribution grounded in evidence].` For non-author mentioned-persons, the pattern degrades to `**Name** is credited with [specific contribution grounded in evidence].` when the `author_context` is absent.
- Required sections for biography articles (minimum of 2):
  - `## Research` or `## Contributions` — what the person did, grounded in evidence chunks
  - `## Publications` — only when `author_context.primary_publications` is non-empty; populated from that context; formatted as a real blank-line-separated markdown list so it renders as `<ul>` (depends on 6C fix)
- Optional sections when evidence supports: `## Education`, `## Career`, `## Collaborations`, `## Legacy`.
- `## References` (required last).
- Explicit banned-phrases list as above.
- Robustness to missing `author_context`: when the writer receives a person page with no `author_context` (a mentioned-not-authored person), the template degrades to a biographical sketch based purely on evidence quotes. The lead might read: `**Chua** is credited with introducing the memristor concept in 1971[^e1].` — short, factual, grounded, no meta-commentary.

#### Files to modify (6E)

- `src/wikify_simple/prompts/artifact_types/wiki_concept.md` → rename to `wiki_article.md` (coordinated with 6D) and rewrite
- `src/wikify_simple/prompts/artifact_types/wiki_person.md` — complete rewrite
- `.claude/skills/wikify_simple/handlers/write.md` — update the article + person guidance to reference the new template rules; add the explicit banned-phrases list; reference the Wikipedia MoS URLs as the authoritative source
- `tests/wikify_simple/test_prompts_layered.py` — assertions that the new templates contain the banned-phrases guidance and the required-section language
- `src/wikify_simple/prompts/style_guide.md` — add the banned-phrases list (it's a project-wide rule, not just per-template)

#### Verification (6E)

- Re-run the scripted test and open 3 article pages + 3 person pages in the browser. Each page starts with a bold title and a one-sentence IS definition. No page contains any banned phrase. Person pages follow the `**Name** (year-range) is a ...` lead pattern. `## References` is always the last section.
- `grep -rn "appears in this corpus\|mentioned in this corpus\|in this corpus" $BUNDLE/articles/ $BUNDLE/people/` — zero hits.
- Publication lists render as real `<ul>` elements (shared with 6C).
- Unit test asserts the new template files contain the key structural markers.

### 6D — Rename `concepts` → `articles`

**Scope**: the internal `kind` literal, the bundle directory, the UI labels, and all test references. The `ExtractedConcept` Pydantic class keeps its name (it represents an extracted item, not specifically an article — minimizes blast radius). The concept terminology stays out of user-visible names.

- `models.py::PageKind = Literal["article", "person"]` (was `"concept"`)
- `contracts/schema.py::ExtractedConcept.kind` — update Literal
- `contracts/schema.py::WriteRequest.page_kind` — update docstring + type hint
- `paths.py::BundlePaths.concepts_dir` → rename property to `articles_dir`, path becomes `articles/`
- `store/wiki_files.py::write_page` — routing uses `articles/` for `kind="article"`
- `store/wiki_index.py` — directory references; extend `migrate_prefixed_page_ids` to also handle `concepts/` → `articles/` directory rename on existing bundles (idempotent)
- `render/html/render.py` — walk `articles/` + `people/`; UI labels "Articles" / "People" in index and navigation
- `.claude/skills/wikify_simple/handlers/extract.md` — update the `kind` rule to `"article" | "person"` and update the routing note (`articles/<id>.md` vs `people/<id>.md`)
- `.claude/skills/wikify_simple/handlers/write.md` — same
- `.claude/skills/wikify_simple/reference/orchestrator.md` — update any "concepts" references
- `docs/refactor/*.md` — update the bundle-shape sections that mention `concepts/`
- **Tests**: find-and-replace `"concept"` → `"article"` where it refers to page kind or directory. Leave `ExtractedConcept` class name alone. Estimated ~30-40 test lines touched.

### Files to modify (Phase 6)

| File | 6A | 6B | 6C | 6D | 6E |
|---|---|---|---|---|---|
| `src/wikify_simple/contracts/schema.py` | ✓ (validator) | ✓ (WriteRequest.author_context) | | ✓ (kind literal) | |
| `src/wikify_simple/models.py` | | | | ✓ (PageKind) | |
| `src/wikify_simple/paths.py` | | | | ✓ (articles_dir) | |
| `src/wikify_simple/distill/write/author_pages.py` → `author_context.py` | | ✓ (rewrite as context builder) | | | |
| `src/wikify_simple/distill/write/requests.py` | | ✓ (attach author_context) | | | |
| `src/wikify_simple/distill/pipeline.py::_finalize_pages` | | ✓ (drop build_author_pages) | | | |
| `src/wikify_simple/render/html/render.py` | | | ✓ (filter + sane_lists) | ✓ (articles label) | |
| `src/wikify_simple/store/wiki_files.py` | | | | ✓ | |
| `src/wikify_simple/store/wiki_index.py` | | | ✓ (filter) | ✓ (migration) | |
| `src/wikify_simple/prompts/artifact_types/wiki_concept.md` → `wiki_article.md` | | | | ✓ (rename) | ✓ (rewrite) |
| `src/wikify_simple/prompts/artifact_types/wiki_person.md` | | | | | ✓ (rewrite) |
| `src/wikify_simple/prompts/style_guide.md` | | | | | ✓ (banned phrases) |
| `.claude/skills/wikify_simple/handlers/write.md` | ✓ | ✓ (person guidance) | ✓ (list hygiene) | ✓ | ✓ |
| `.claude/skills/wikify_simple/handlers/extract.md` | | | | ✓ | |
| `tests/wikify_simple/test_write_validator.py` | ✓ | | | | |
| `tests/wikify_simple/test_author_context.py` **NEW** | | ✓ | | | |
| `tests/wikify_simple/test_author_pages.py` | | ✓ (delete) | | | |
| `tests/wikify_simple/test_html_render.py` | | | ✓ | ✓ | |
| `tests/wikify_simple/test_wiki_index.py` | | | ✓ | ✓ | |
| `tests/wikify_simple/test_prompts_layered.py` | | | | | ✓ |

### Verification (Phase 6)

- Re-run the 3-iter scripted test (same parameters as before). Expected:
  - Every written article page has at least 2 in-body H2 sections before `## References`, visible in rendered HTML.
  - Every written people page starts with the person's name in bold followed by biographical prose, NOT "appears in this corpus...".
  - Bullet lists in any page render as real HTML `<ul>` elements; no run-on text.
  - The wiki index and the HTML landing page list only real pages (no skeletons).
  - The index labels are "Articles" and "People", not "Concepts" and "People".
  - `articles/` directory exists in the bundle; `concepts/` does not (after migration).
- Unit tests from each 6A-6D sub-phase pass.
- `test_author_pages.py` is gone; `test_author_context.py` exists.
- Grep `src/wikify_simple/` for `"concept"` in a `kind=` or directory context — zero hits. (The class `ExtractedConcept` stays.)

---

## Execution order and parallelization

```
Phase 6 (output quality) + Phase 1 (ingest pre-compute) ← run in parallel
     │
     ├── Phase 6A article validator       ← parallel, small
     ├── Phase 6B people-via-models       ← parallel, medium, depends on 6D's kind literal
     ├── Phase 6C renderer filter         ← parallel, small
     ├── Phase 6D rename concepts→articles ← must land before 6B to avoid double-touch
     │
     ├── Phase 1 ingest pre-compute       ← parallel with 6, blocks 2/3/4
     │
     └── Phase 5A/5C (latency + budget)   ← parallel with everything, both small

After Phase 1 lands:
     ├── Phase 2 (campaign driver)
     ├── Phase 3 (LLM-as-sampler)
     ├── Phase 4 (image first-class)
     └── Phase 5B (prompt length)
```

**Parallelization plan**:
- **Batch 0 (parallel, immediately)**: Phase 6 sub-phases + Phase 1 + Phase 5A + Phase 5C. These are mostly independent.
  - Sequence **6D before 6B** because 6B touches `contracts/schema.py::WriteRequest` and 6D touches the same file's `kind` literal — landing 6D first avoids merge work on `ExtractedConcept.kind`.
  - 6A and 6C are independent of both 6B and 6D.
  - Phase 1 is independent of all of Phase 6 (different files).
  - Phase 5A shares `pipeline.py` and `bindings/file_dispatch.py`; it can run in parallel with Phase 1 and Phase 6 but watch for `pipeline.py` conflicts.
  - Phase 5C shares `pipeline.py` with 5A; sequence them or merge.
- **Batch 1 (serial blocker)**: Phase 1 alone must finish before 2/3/4 start, because they all consume `sampler_index.json`.
- **Batch 2 (parallel after Phase 1)**: Phase 2, Phase 3, Phase 4, and Phase 5B. Conflicts:
  - Phases 1, 2, 4, 5C all edit `distill/pipeline.py` — sequence or accept merges
  - Phases 3, 4, 5B, 6B all edit `contracts/schema.py` — sequence or accept merges

Approximate sizing:
- Phase 1: small-medium (1 new module, 3 edited, 1 new test)
- Phase 2: medium (new CLI verb, refactor `run` into helpers, new test)
- Phase 3: medium-large (new actions + tools inside orchestrator handler, schema change, test updates, skill rewrites)
- Phase 4: medium (sampler changes, metric additions, schema field, handler + metric tests)
- Phase 5A: small (config constant + `extract_many` method + inner-loop rewrite)
- Phase 5B: medium (prompt audit + hash-based layer delivery + schema + handler skill + runtime skill)
- Phase 5C: small (extract-loop condition + per-write pre-check + new test)
- Phase 6A: small (validator tweak + handler guidance + one test)
- Phase 6B: medium (rewrite `author_pages` as context builder + writer handler person-page guidance + schema field + delete old tests + write new ones)
- Phase 6C: small (renderer filter + index filter + `sane_lists` extension)
- Phase 6D: medium (literal rename + directory rename + migration + ~30 test touches)

---

## Critical files (reference)

| File | Used by phase |
|---|---|
| `src/wikify_simple/paths.py` | 1, 5B, 6D |
| `src/wikify_simple/models.py` | 6D |
| `src/wikify_simple/ingest/refresh.py` | 1 |
| `src/wikify_simple/ingest/sampler_index.py` **NEW** | 1, 4 |
| `src/wikify_simple/distill/pipeline.py` | 1, 2, 4, 5A, 5B, 5C, 6B |
| `src/wikify_simple/distill/preload.py` **NEW** | 2 |
| `src/wikify_simple/distill/sampler.py` | 1, 3, 4 |
| `src/wikify_simple/distill/policy.py` | 3, 4 |
| `src/wikify_simple/distill/write/requests.py` | 4, 5B, 6B |
| `src/wikify_simple/distill/write/author_pages.py` → `author_context.py` | 6B |
| `src/wikify_simple/contracts/schema.py` | 3, 4, 5B, 6A, 6B, 6D |
| `src/wikify_simple/contracts/protocols.py` | 5A |
| `src/wikify_simple/bindings/file_dispatch.py` | 5A |
| `src/wikify_simple/infra/config.py` | 5A |
| `src/wikify_simple/eval/metrics.py` | 4 |
| `src/wikify_simple/render/html/render.py` | 6C, 6D |
| `src/wikify_simple/store/wiki_files.py` | 6D |
| `src/wikify_simple/store/wiki_index.py` | 6C, 6D |
| `src/wikify_simple/cli.py` | 2, 4 |
| `src/wikify_simple/prompts/registry.py` | 5B |
| `src/wikify_simple/prompts/style_guide.md` + `fields/*.md` + `artifact_types/*.md` | 5B |
| `.claude/skills/wikify_simple/handlers/orchestrate.md` | 3 |
| `.claude/skills/wikify_simple/handlers/extract.md` | 4, 6D |
| `.claude/skills/wikify_simple/handlers/write.md` | 4, 5B, 6A, 6B, 6C, 6D |
| `.claude/skills/wikify_simple/runtime/serve-dispatch.md` | 5A, 5B |
| `.claude/skills/wikify_simple/reference/orchestrator.md` | 3, 4, 6D |
| `.claude/skills/wikify_simple/workflows/run-scripted.md` | 2 |
| `.claude/skills/wikify_simple/workflows/run-campaign.md` | 2 |

## Functions to reuse (do not rewrite)

- `distill/sampler.py::apply_coverage_feedback` (line 149) — extend for captions, don't replace
- `distill/sampler.py::init_coverage_state` (line 111) — add caption init, don't replace
- `distill/sampler.py::restore_coverage_state` (line 129) — no change, works as-is
- `distill/iteration.py::save_coverage_memory` / `load_coverage_memory` — no change
- `store/images_index.py::ImageIndex.for_doc` / `.resolve` / `.all_records` — reuse for handler image-awareness
- `ingest/images.py::caption_chunks_for` — already produces the caption chunks; Phase 1 picks them up via the `section_path=["__image__", ...]` marker
- `distill/policy.py::PolicyRuntime` — reuse for tier/allocation state, no change
- `distill/policy.py::LlmPolicy._execute_orch_action` — extend the match/case, do not replace
- `eval/metrics.py::coverage_residual` — model the new `image_coverage_residual` on it directly

---

## Verification checklist (run after all phases land)

1. `uv run python -m wikify_simple.cli ingest data/papers/mvp20 --out /tmp/mvp20_check` produces `sampler_index.json`, `pagerank.json`, and the existing artifacts.
2. `uv run python -m wikify_simple.cli campaign --strategy M --iterations 3 --budget 50000 --corpus /tmp/mvp20_check --bundle /tmp/mvp20_bundle` runs all 3 iterations in one Python process.
3. `uv run python -m wikify_simple.cli distill --phase write --bundle /tmp/mvp20_bundle ...` does not load the vector store or corpus graph (verify via stderr instrumentation).
4. With `--mode guided`, the LLM campaign uses `pick_chunks` at least once and `jump_figures` at least once, visible in `_run.json::policy_actions`.
5. `uv run python -m wikify_simple.cli eval --bundle /tmp/mvp20_bundle --corpus /tmp/mvp20_check` reports `image_coverage_residual`, `figure_reference_rate`, and `n_figures_referenced_in_bodies` in `_metrics.json`.
6. `_run.json::budget_used_haiku_eq` never exceeds 105 % of the target on any iteration (Phase 5C reservation in action).
7. `_run.json::by_role.writer.input_tokens` per call drops ~50 % after Phase 5B lands (fixed prompt layers delivered once per serve-dispatch session).
8. `_run.json::wall_seconds` for a 10-extract + 2-write iteration drops at least 2× relative to the pre-5A baseline (parallel `extract_many`).
9. `uv run pytest tests/wikify_simple/ -q` — all tests pass (including the new ones from each phase).
10. `uv run ruff check src/wikify_simple/ tests/wikify_simple/` — clean.
11. `uv run --with pyright pyright src/wikify_simple/` — 0 errors.
12. Wall-time smoke: on mvp20_v6, iteration 1 setup time drops from ~1000 ms to <100 ms.
13. Grep `src/wikify_simple/` for `anthropic` and `openai` — zero new imports from Phase 5 (vendor-neutrality check).
14. **Rendered HTML review** (Phase 6): open 3 article pages, 3 people pages, and the index in a browser. Confirm: every article has ≥2 in-body H2 sections before References; every people page starts with the person's name and biographical prose (no "appears in this corpus"); all bullet lists render as real `<ul>` elements; index labels say "Articles" and "People"; no skeleton pages appear anywhere.
15. **Zero `"concept"` in `kind=` or directory contexts** (`grep -rn '"concept"' src/wikify_simple/` in `kind=` or directory scope — zero hits; `ExtractedConcept` class name stays).
16. **Zero deterministic people-page generation**: `grep -rn 'build_author_pages' src/wikify_simple/` — zero hits in production code.
