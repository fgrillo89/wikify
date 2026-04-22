# Distill-test readiness and replay plan

Pre-study review of the distill pipeline against `ald_all_marker`, plus
the telemetry needed for exploration replay. This document distinguishes:

- **Confirmed in code**: verified against the current implementation.
- **Observed on `ald_all_marker`**: corpus-specific findings that should
  be tied to a reproducible artifact or script.
- **Proposed telemetry**: useful for replay and diagnosis, but not yet
  required for the pipeline to function.

## Corpus state (ald_all_marker)

- 208 corpus docs, 4985 chunks, 2243 figures, 2409 equations, 775 authors,
  7340 external cited works.
- Vectors: `jinaai/jina-embeddings-v2-small-en` (512-d).
- `knowledge_graph.json` built, PageRank computed on 205/208 corpus
  sources.

## Code state: yellow

Six issues are load-bearing. They must be resolved (fix or document) before
strategy comparisons are trustworthy.

### Issue 1 — PageRank on corpus papers is nearly flat

Confirmed in code:
- PageRank is computed on `CITES` edges only.
- `docs/strategies.md` currently describes PageRank as living on
  `cites ∪ doc_similar`, which does not match the implementation.

Observed on `ald_all_marker`:
- corpus-source PageRank spread is narrow enough that the signal may be
  weak for within-corpus ranking.

Consequence:
- the current pure global-jump preset provides a weaker contrast than
  intended on this small corpus;
- for the renamed follow-on design, this mainly affects the future
  `high-exploration` condition rather than the immediate
  `baseline / balanced / guided` table.

Decision:
- Keep PageRank strict: compute it on **corpus-to-corpus citation edges
  only**.
- Do **not** include `doc_similar` edges in PageRank. Similarity edges
  are useful for traversal, but not clean enough for centrality because
  they are constructed from capped nearest-neighbor rules.

Action:
- Update docs to match the intended strict corpus-citation definition.
- Re-check PageRank spread after this change on the larger target corpus.

### Issue 2 — Replay consumers must read edge `kind`, not edge `type`

Confirmed in code:
- serialized edges use `kind`, not `type`;
- runtime graph consumers also read `kind`.

Consequence: any replay or animation tool that expects `edge.type` will
see `None` for every edge, even though the information is present under
`kind`.

Decision:
- Standardize replay tooling on `kind`. This matches the current KG
  runtime and avoids inventing a second edge-field convention.

This is a schema-contract issue, not a graph-loss bug or a distill
correctness bug.

### Issue 3 — `refine_uncertain` is not uncertainty-based

`_local_refine_uncertain` sorts `coverage_residuals` descending and picks
the first unseen. That is functionally identical to `coverage_gap`
restricted to touched chunks. It does NOT read per-chunk entropy from
cached extraction output. Either wire it to cached logprobs/alternates,
or drop the option from the ablation table. `docs/strategies.md` Open
Questions already flags this.

### Issue 4 — Bootstrap diverges between docs and implementation

Docs promise an "abstract sweep" as round zero — one abstract per doc,
`min(n_docs, 0.1 × total_budget)` chunks. Code only enforces
`jump_rate=1` while `wiki_is_empty` and picks doc-then-3-chunks per
`global_op`. For the current pure global-jump preset, the first few picks land on high-PR
docs but do NOT sweep all 208 abstracts.

Decision for the main small-scale run:
- bootstrap is **off** for all three main conditions;
- seeded bootstrap will be evaluated later as a side experiment, not as
  part of the first comparison table.

If/when seeded bootstrap is tested, define it explicitly as:
- the same seed rule for every condition;
- seed documents selected by corpus-citation PageRank plus a submodular
  embedding-coverage objective;
- one abstract-equivalent chunk per selected document;
- seed size capped by budget, not by "all abstracts in corpus".

### Issue 5 — `coverage_gap` keeps popping captions after text exhausts

`_global_coverage_gap` pops the highest-residual entry from a unified
heap. Text chunks default to residual 1.0, captions to 0.8. Once every
text chunk is seen, the heap starts feeding captions to the extractor
as if they were prose. ALD captions are 1–2 lines; extract produces
empty or near-empty dossier entries, bloating
`n_empty / n_total` against the playbook's 20% threshold.

Fix: either gate captions behind `GlobalOp.FIGURES` only (cleanest) or
pin captions to terminal residual after exhaust so they never refill
the main heap. ~20 LOC.

### Issue 6 — Guided `write_now` can spend into the reserved write headroom

`pipeline.run` holds `expected_write_reserve = split.write_haiku_eq *
0.95` off-limits during extract. Guided's mid-session `write_now`
bypasses this because `_run_write_pass` is called mid-extract with its
own `1.05 × budget` guard. In practice the orchestrator can spend the
reserved headroom before the final write pass.

Decision:
- accept this for `guided` and document it explicitly as part of the
  treatment;
- scripted/default conditions keep fixed reserve semantics, while
  `guided` is allowed to re-time spending via `write_now`.

## Strategy business logic — end-to-end inventory

| Knob | Type | Where it lives | Mutable mid-run? |
|---|---|---|---|
| `local_op` | none / similarity_walk / refine_uncertain | `LevyExplorer.local_op` | no |
| `global_op` | uniform / pagerank / coverage_gap / figures | `LevyExplorer.global_op` | no |
| `jump_rate` | float [0, 1] | `LevyExplorer.jump_rate` | no |
| `chunks_per_landed_doc` | locked = 3 | `LevyExplorer` | locked |
| `exploit_fraction` | float [0, 1] | `StaticBudget` / `AdaptiveBudget` + `RuntimeOverrides` | yes (`set_allocation`, full tools only) |
| `extract_tier`, `write_tier`, `edit_tier`, `compact_tier` | S / M / L | `StrategyConfig` + `RuntimeOverrides` | yes (`set_tier`, full tools only) |
| `orchestrate_tier` | locked = L | `StrategyConfig` | locked |
| `dedup_after_extract` | locked on | canonicalize | locked |
| `bootstrap_rule` | docs say "abstract sweep"; implementation differs | — | see Issue 4 |

### How the explorer touches the corpus per step

1. `LevyExplorer.next_batch(state, k=4)` picks k chunk_ids.
2. Per pick: `apply_coverage_feedback(state, cid, as_evidence=False)`
   runs `kg.chunks().similar_to(cid, top_k=5)` — a cosine scan over all
   4985 chunk vectors. ~5 ms each.
3. Same call fires again with `as_evidence=True` after extraction
   returns at least one concept.
4. At `write_now` or end-of-run, every evidence chunk is re-fed through
   `apply_coverage_feedback`.

Per 1k extract-budget the hot path fires ~80–120 vector scans. No
change needed.

### How pages are born

1. Chunk read → `ExtractRequest` dispatched → concepts returned.
2. Each concept becomes a `Candidate(concept, chunk_id, doc_id)`.
3. `canonicalize(candidates, existing=existing_pages)` merges candidates
   by normalized title/aliases into `WikiPage` instances.
4. `DossierStore` persists aggregated evidence per page.
5. `WriteRequest` built per page; dispatched to writer.

The wiki graph is built **after** all writes finish. The explorer never
sees the wiki graph this run; only `pages_concept_evidence_chunks`
(flat list of evidence chunk ids) feeds back. Correct scope for v1.

## Practical-choices inventory

All calibration numbers that shape the outcome. None is called out as a
study knob; all must land in `_run.json` so a curve difference can be
traced to a parameter change.

| Choice | Current value | Site | Risk |
|---|---|---|---|
| PageRank alpha | 0.85 | `graph_build.py:367` | low |
| PageRank input graph | `cites` only (Paper → Paper) | `graph_build.py` | **high** — Issue 1 |
| PageRank storage | `node.pagerank` on source nodes | read by `_build_explorer_state` | low |
| PageRank missing-value fallback | uniform `1 / n_docs` | `pipeline.py:878` | low |
| Coverage residual init (text) | 1.0 | `init_coverage_state` | low |
| Coverage residual init (caption) | 0.8 | `init_coverage_state` | **medium** — Issue 5 |
| Coverage init boost (citation_count > 3) | ×1.2 cap 1.0 | `init_coverage_state` | low (only 3 such papers in ald) |
| Neighbor discount floor (read-only) | 0.35 | `apply_coverage_feedback` | medium |
| Neighbor discount floor (evidence) | 0.20 | `apply_coverage_feedback` | medium |
| Doc-level discount floor | 0.65 / 0.50 / 0.35 for 1 / 2 / 3+ reads | `apply_coverage_feedback` | medium |
| Neighbor `similar_to` top_k | 5 (feedback), 8 (similarity walk), 10 (KG tool) | three sites | low |
| `similar_to` scope | all 4985 chunks unless `source(doc_id)` is used | `similarity_walk` | low |
| Batch size | 4 chunks | `extract_batch_size` default | low |
| Guided action cache | 8 batches for active exploration; never for `pick_chunks` or control | `GuidedMode.persist_batches` | medium — affects cost |
| Max concepts per run | 60 | `max_concepts` | medium |
| Cost meter hard abort | 1.05 × budget | `meter.py` | low |

## Adjustment checklist

### Immediate blockers for the small-scale run

- [ ] **Issue 1: PageRank fix.** Make docs and code agree on strict
      corpus-to-corpus citation PageRank. Re-write `knowledge_graph.json`
      once for the small-scale run.
- [ ] **Issue 4: bootstrap decision.** Turn bootstrap into an explicit,
      optional seeded variant and update docs to stop promising a full
      abstract sweep by default.
- [ ] **Issue 5: gate captions.** Text-only coverage_gap; captions
      reached only via `global_op=figures`.
- [ ] **Condition naming sync.** Make the new canonical names visible in
      the small-scale study surfaces and document the alias/migration map.
- [ ] **Baseline rewrite.** Replace the current baseline with the
      explicit abstract-first baseline defined below.
- [ ] **Guided reserve rule.** Keep `guided` write-reserve behavior as an
      accepted treatment difference and document it consistently.

### Follow-on blockers

- [ ] **Issue 1 follow-on validation.** Re-check PageRank spread on the
      larger target corpus after the strict corpus-citation change.
- [ ] **Issue 2: lock the replay edge schema.** Replay tooling must read
      edge `kind`, not edge `type`.
- [ ] **Issue 3: `refine_uncertain`.** Wire to real entropy signal
      OR drop from the broader condition table.
- [ ] Per-step exploration record with residual snapshots.
- [ ] Evidence-birth events (chunk → page mapping with timestamp).
- [ ] KG subgraph export keyed by corpus (one-off per corpus).
- [ ] Coverage-residual frames at sparse checkpoints (every N steps).

### Sanity smoke

- [ ] `balanced --budget 0.1x --seed 0` on `ald_all_marker`.
- [ ] Verify `_run.json::policy_actions` and
      `n_cached_skipped + n_new_extracted == len(chunks_read)`.
- [ ] Confirm `_meta/io_lineage/<run_id>/` emits all three files.
- [ ] `eval` passes M6 grounding gate on the smoke run.

### Regression tests required

- [ ] PageRank graph-input test: asserts exactly which edge kinds
      participate in PageRank.
- [ ] Bootstrap test: when seeded bootstrap is enabled, it follows the
      explicit seed-set definition and budget cap.
- [ ] Coverage-gap caption test: `coverage_gap` never returns caption
      chunks.
- [ ] Guided `write_now` behavior test: reserve-spending differences are
      explicitly accepted and logged.

## Immediate Run Plan

This section is the current source of truth for the first small-scale
comparison. It is intended to be concrete enough to translate directly
into an implementation action plan.

### Goal

Run a small-scale comparison on `ald_all_marker` that is strong enough
to validate the refactored study shape and leave the codebase ready for
the larger follow-on corpus study.

### Conditions

Use the **new condition names as canonical names**. This requires a code
change in strategy naming, docs, and CLI preset surfaces.

Main comparison table:
- `baseline`
- `balanced`
- `guided`

Planned follow-on condition set after the small-scale run:
- `baseline`
- `no-navigation`
- `high-exploration`
- `balanced`
- `high-exploitation`
- `guided`

Migration map for current code/doc terminology:
- current `scripted-mixed` -> new canonical `balanced`
- current `guided-full` -> new canonical `guided`
- `no-navigation`, `high-exploration`, and `high-exploitation` are
  follow-on named conditions that require code changes before use

### Campaign shape

- corpus: `ald_all_marker` only
- budgets: `0.1x / 1x / 3x`
- seed count: 1
- iterations: `create + refine`
- bootstrap: off for the main comparison table

### Fixed run controls

- model tiers fixed for non-guided conditions
- use `extract=S`, `write=M`, `edit=M`, `compact=S`
- budget split fixed at `60 / 35 / 5` for `extract / write / curate`
- this split is provisional and should be revisited after a measurement
  phase on page yield, evidence density, and write throughput

### Exact condition semantics

`balanced`
- scripted mode
- explorer = current mixed explorer policy:
  `local_op=similarity_walk`, `global_op=coverage_gap`,
  `jump_rate=0.1`
- fixed tiers and fixed `60 / 35 / 5` split

`guided`
- guided mode
- allowed actions/tools:
  `walk_local`, `jump_uniform`, `jump_pagerank`, `jump_gap`,
  `jump_figures`, `pick_chunks`, `sample_chunks`, `write_now`,
  `search_chunks`, `get_source_info`, `list_sources`, `get_citations`,
  `get_coverage`, `get_pages`, `get_budget`, `done`,
  `set_allocation`, `set_tier`
- fallback explorer = the same explorer policy as `balanced`
- starts from the same default tiers and split as `balanced`
- may change navigation, allocation, tiers, and write timing mid-run
- reserve-spending differences caused by `write_now` are accepted as
  part of the guided treatment

### Baseline definition

The baseline should be replaced with a new abstract-first,
source-grounded retrieve-and-synthesise condition.

Required behavior:
- no active navigation loop
- no graph walk after each read
- use the same default tiers as `balanced`
- use the same `60 / 35 / 5` split at the run level
- partition the baseline extract budget as:
  - 1/3 for abstract seeding
  - 2/3 for post-seed evidence retrieval
- seed documents selected by corpus-citation PageRank plus a submodular
  embedding-space coverage objective
- one abstract-equivalent chunk per selected document
- stop abstract seeding when the next abstract chunk would exceed the
  seed-phase budget cap
- candidate concepts extracted from those seed chunks
- evidence gathered afterward with plain chunk similarity search only
- use document embeddings for coverage defined as mean-pooled chunk
  embeddings over non-reference, non-caption chunks
- greedy seed selection objective:
  for candidate doc `d` and selected seed set `S`, maximize
  `0.7 * pr_norm(d) + 0.3 * coverage_gain(d | S)`
- define `coverage_gain(d | S)` as the increase in
  `sum_u max_{s in S} max(0, cos(e_u, e_s))` over all corpus docs `u`
  when `d` is added, where `e_x` is the document embedding and cosine
  similarity is clipped at zero
- retrieval default for support gathering:
  `top_k = 8` chunks per candidate page, with at most 2 chunks per
  source document in the final retrieved set
- baseline refine may create new pages if still within budget
- `create + refine` uses the same run-level split; refine remains
  non-agentic and uses the same similarity-retrieval rule

### What this first run is for

- validate the new baseline against `balanced` and `guided`
- validate the new naming and study framing
- measure whether the fixed `60 / 35 / 5` split is sensible
- leave the codebase ready to add `no-navigation`,
  `high-exploration`, and `high-exploitation` cleanly

## Follow-on Readiness Constraints

Changes made for the small-scale run should keep the codebase in a good
state for the larger follow-on study.

### Naming and study surfaces

- new condition names should become canonical in docs and code
- avoid keeping `E / M / X` as the primary study surface
- old names may survive as temporary aliases only if needed for
  migration, not as the conceptual vocabulary

### Strategy design constraints

- PageRank remains strict corpus-to-corpus citation centrality
- similarity edges may guide traversal, but not centrality
- bootstrap must be explicit, optional, and reusable across conditions
- baseline remains non-agentic even after refine is added

### Budgeting constraints

- fixed scripted/default split for comparability
- guided remains the only adaptive-allocation condition
- keep the split measurable and easy to revise after pilot results

### Study scalability constraints

- decisions on `ald_all_marker` should scale to a much larger corpus
- avoid designs that require "all abstracts in corpus" passes by default
- prefer budget-capped seeding and explicit condition definitions

## Implementation Worklist

This worklist is grouped so it can be translated into parallel
implementation tasks.

### 1. Strategy naming and study surfaces

- rename canonical conditions to `baseline`, `no-navigation`,
  `high-exploration`, `balanced`, `high-exploitation`, `guided`
- update study docs to use the new names throughout
- update CLI preset surfaces and any study-driver assumptions
- clearly note that this is a code change, not a docs-only relabel

### 2. Baseline rewrite

- replace the current baseline with the new abstract-first baseline
- implement budget-capped seed selection
- implement PageRank-plus-submodular seed selection
- keep evidence retrieval as plain chunk similarity search
- support `create + refine`, including baseline refine creating new pages
  within budget

### 3. PageRank and centrality cleanup

- make code and docs agree on strict corpus-citation PageRank
- remove doc references that imply `doc_similar` participates in PageRank
- verify PageRank spread after the fix on small and later large corpora

### 4. Bootstrap cleanup

- remove the implied default abstract-sweep contract from docs
- keep bootstrap off for the main small-scale run
- define seeded bootstrap as an optional side experiment with:
  PageRank-plus-submodular seed set, one abstract-equivalent chunk per
  selected document, and a hard budget cap

### 5. Coverage and write-reserve fixes

- gate captions out of `coverage_gap` unless explicitly using figures
- document that guided `write_now` may spend into reserved write
  headroom and that this is an accepted part of the guided treatment

### 6. Telemetry and replay readiness

- persist step-level picks in replay telemetry
- add residual before/after snapshots and page-birth events
- standardize replay tooling on edge `kind`
- choose the first viewer target after the main study surfaces are stable

## Replay / diagnostic plan — graph-replay of exploration

Current telemetry is useful for run audit, but not replay-grade:
- `_run.json` already records `chunks_read`, `policy_actions`,
  budget splits, and write rejections.
- `_meta/io_lineage/<run_id>/` already records chunk metadata and
  extractor output summaries.
- Guided snapshots already compute residual histograms.

Still missing for replay:
- `policy_actions` does not persist the actual picks per step.
- `chunks_read` is ordered, but has no per-step context.
- `_trace.jsonl` (KG trace in `citestore/graph.py`) is opt-in and only
  captures search / similar_to / collect terminal calls. Distill does
  not call `enable_trace()`.
- No residual snapshots, no evidence-birth timestamps.

### Data model — `<bundle>/_meta/explore_trace.jsonl`

Append-only JSONL. One line per step (extract) or event (write).

Extract step:

```json
{
  "step": 42,
  "t": "2026-04-18T12:34:56.789Z",
  "phase": "extract",
  "action": "walk_local",
  "op": {"level": "local", "kind": "similarity_walk"},
  "seed_chunk_id": "doc5#c12",
  "picks": [
    {
      "chunk_id": "doc5#c17",
      "doc_id": "doc5",
      "pagerank_doc": 0.00038,
      "residual_before": 0.92,
      "residual_after": 0.20,
      "similarity_to_seed": 0.81,
      "section_type": "results",
      "is_caption": false
    }
  ],
  "budget_spent_cum": 23421.5,
  "budget_delta": 412.0,
  "novelty_rate_window": 0.73,
  "residual_histogram": [12, 45, 102, 833, 3993],
  "n_seen_cum": 63,
  "n_pages_cum": 4,
  "n_candidates_cum": 19
}
```

Write event:

```json
{
  "step": 85,
  "t": "...",
  "phase": "write",
  "event": "page_birth",
  "page_id": "atomic-layer-deposition",
  "evidence_chunk_ids": ["doc5#c17", "doc9#c4"],
  "n_evidence": 6
}
```

### Emission sites

| Source | Hook |
|---|---|
| `LevyExplorer._local` / `_global` | wrap to capture the `op` actually used (including forced-global-on-empty) |
| `apply_coverage_feedback` | read residual before + after, emit per pick |
| `ScriptedMode.next_extract` / `GuidedMode.next_extract` | step counter, action, cached flag (extend existing `policy_events`) |
| Write pass | per page, evidence chunk ids at birth |
| Coverage histogram | 5-bucket count on `coverage_residuals` at end of each step |
| PageRank of picked doc | from `state.pagerank_doc` |

Implementation: one `ExploreRecorder` injected into the explorer (or
wrapping the mode), flushed after each `next_batch`. Zero model-call cost.

### Static graph artifact — `<corpus>/explore_graph.json`

Exported once per corpus, reused across runs:

- **nodes**: `{id, kind (corpus / cited), title, pagerank, is_corpus}`
  for all source nodes; `{id, doc_id, section, is_caption}` for chunks.
- **edges**: `{src, dst, kind}` for `cites`, `doc_similar`,
  `co_section`, `authored_by`.
- **layout**: precomputed 2D coordinates (force-directed or UMAP on doc
  embeddings) so the viewer does not have to lay out ~20k nodes.

### Viewer — two modes

**Publication panels (static).** `scripts/render_explore_panels.py`:
- reads `explore_graph.json` + `explore_trace.jsonl`;
- draws 4–6 panels at evenly-spaced steps (t = 0, 25%, 50%, 75%, 100%);
- each panel: grey base graph, visited chunks coloured by residual,
  edges traversed in the last window highlighted, page-birth markers;
- matplotlib or altair; output to
  `<bundle>/_meta/figures/explore_panels.{svg,pdf}`.

**Animated GIF / mp4.** `scripts/render_explore_video.py`:
- same inputs, ~300-frame video;
- `imageio` for GIF, `ffmpeg` for mp4;
- optional step counter + residual histogram inset.

**Interactive HTML (stretch).** d3 or Sigma.js single-page viewer over
the two JSONs with timeline scrub. Not mandatory for the core study;
useful for diagnosis.

## Effort estimate

| Piece | Rough effort |
|---|---|
| Recorder + trace emission | ~150 LOC in `explorer.py` + pipeline hook |
| `explore_graph.json` export | ~80 LOC, one-time per corpus |
| Issue 1: PageRank fix | ~30 LOC in `graph_build.py` |
| Issue 2: replay schema cleanup (`type` -> `kind` in viewers/tools) | ~10 LOC |
| Issue 5: caption gating | ~20 LOC |
| Issue 3: `refine_uncertain` (wire or remove) | ~20 LOC |
| Issue 4: bootstrap definition + optional seeded variant | ~30 LOC |
| Static panels script | ~200 LOC |
| Animated video script | ~150 LOC |
| Smoke run + Playbook Part 5 review | half-day |

## Deferred non-core improvements

Useful, but out of scope for pre-study readiness:
- reviewer ergonomics such as `hot.md`, `runs.md`, and study
  `comparison.md`;
- lint/report surfaces (`wikify lint --bundle`);
- richer writer feedback such as contradiction and gap callouts;
- Obsidian overlays for corpus/wiki browsing.

These can improve inspection and iteration, but they should not block
the first trustworthy strategy-comparison run.

## Open decisions

1. **Replay viewer target.** Static panels only, GIF/mp4 as well, or
   interactive HTML on top.
   Decision: static panels plus animation; interactive HTML remains
   optional stretch work.
2. **Bootstrap side experiment timing.** Seeded bootstrap is optional for
   now and is not a near-term blocker. Get the main comparison working
   first; revisit seeded bootstrap after the core pipeline is in place.
