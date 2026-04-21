# Distill-test readiness and replay plan

Pre-study review of the distill pipeline against `ald_all_marker`, plus
the telemetry plan needed to produce publication-grade exploration
figures. Written before the first full-scale strategy comparison run.

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

Spread over 205 corpus sources: top/bottom ≈ 0.00048 / 0.00013 ≈ **3.7×**.
The citation graph is dominated by corpus→external edges (208 corpus
papers cite into the 7340-node `kind="cited"` pool), so intra-corpus
PageRank carries weak signal.

Consequence: `scripted-explore` (`global_op=pagerank`, `jump_rate=1.0`)
behaves close to `scripted-uniform`. That collapses axis A for the E
anchor cell.

Fix options:
- **(a)** Compute PageRank on `cites ∪ doc_similar` as
  `docs/strategies.md` promises. Current code
  (`citestore/graph_build.py:367`) uses `cites` only.
- **(b)** Use personalized PageRank restricted to corpus nodes (restart
  on the 208-node corpus set), which sharpens within-corpus structure.
- **(c)** Accept flat signal; document it as a finding.

Lean: (a) first. Matches docs, cheapest, one-line semantic change.

### Issue 2 — Edge `type` missing from serialized `knowledge_graph.json`

`Counter(e.get('type') for e in edges)` over the saved JSON returns
`{None: 41496}`. Runtime `KnowledgeGraph` has the types; the export
drops them. Not breaking distill today (types are reconstructed during
`build_knowledge_graph`), but any replay/animation tool that reads the
raw JSON has no way to distinguish `cites`, `doc_similar`, `co_section`,
`authored_by`. Fix in the graph-write path. ~10 LOC.

### Issue 3 — `refine_uncertain` is not uncertainty-based

`_local_refine_uncertain` sorts `coverage_residuals` descending and picks
the first unseen. That is functionally identical to `coverage_gap`
restricted to touched chunks. It does NOT read per-chunk entropy from
cached extraction output. Either wire it to cached logprobs/alternates,
or drop the option from the ablation table. `docs/strategies.md` Open
Questions already flags this.

### Issue 4 — Bootstrap under-specified in code vs. docs

Docs promise an "abstract sweep" as round zero — one abstract per doc,
`min(n_docs, 0.1 × total_budget)` chunks. Code only enforces
`jump_rate=1` while `wiki_is_empty` and picks doc-then-3-chunks per
`global_op`. For `scripted-explore` the first few picks land on high-PR
docs but do NOT sweep all 208 abstracts.

For a convergence-curve study the bootstrap must be identical across
strategies. Decision required:
- implement explicit abstract sweep for round zero, or
- keep current behaviour and update `docs/strategies.md` locked-constants
  table.

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

### Issue 6 — Write-budget reserve vs. guided mid-session writes

`pipeline.run` holds `expected_write_reserve = split.write_haiku_eq *
0.95` off-limits during extract. Guided-full's mid-session `write_now`
bypasses this because `_run_write_pass` is called mid-extract with its
own `1.05 × budget` guard. In practice the orchestrator can spend the
reserve before the final write pass. Not a bug; needs to be documented
so E/M/X and guided results are comparable.

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
| `bootstrap_rule` | locked "abstract sweep" — **not implemented** | — | see Issue 4 |

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

## Adjustment checklist (before the first full-scale run)

### Must-fix

- [ ] **Issue 1: PageRank fix.** Union `cites ∪ doc_similar`, or switch
      to personalised PageRank on the corpus node set. Re-write
      `knowledge_graph.json` once.
- [ ] **Issue 2: serialize edge `type`** in `graph_build` save path.
- [ ] **Issue 4: bootstrap decision.** Implement abstract sweep OR
      remove the row from the locked-constants table.
- [ ] **Issue 5: gate captions.** Text-only coverage_gap; captions
      reached only via `global_op=figures`.
- [ ] **Issue 3: `refine_uncertain`.** Wire to real entropy signal
      OR drop from ablation list.

### Telemetry add-ons for replay (see next section)

- [ ] Per-step exploration record with residual snapshots.
- [ ] Evidence-birth events (chunk → page mapping with timestamp).
- [ ] KG subgraph export keyed by corpus (one-off per corpus).
- [ ] Coverage-residual frames at sparse checkpoints (every N steps).

### Sanity smoke

- [ ] `scripted-mixed --budget 0.1x --seed 0` on `ald_all_marker`.
- [ ] Verify `_run.json::policy_actions` and
      `n_cached_skipped + n_new_extracted == len(chunks_read)`.
- [ ] Confirm `_meta/io_lineage/<run_id>/` emits all three files.
- [ ] `eval` passes M6 grounding gate on the smoke run.

### Study-run scope (needs a decision)

- Corpus: `ald_all_marker` only, or replicate on a second corpus?
- Presets: 5 anchor cells (E, M, X, guided-navigate, guided-full) at
  3 budgets × 3 seeds = **45 runs**, or include the 21 ablation cells
  from `strategies.md` (+66 runs)?
- Baseline: include `baselines/pipeline.py` (retrieve-and-summarise)?
  +1 run per (budget × seed).

## Replay / diagnostic plan — graph-replay of exploration

Current telemetry is too thin for an animation:
- `policy_actions` records the action and counts but not the actual
  picked chunks (only `reason` for `pick_chunks`).
- `chunks_read` is an ordered list of ids with no per-step context.
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
wrapping the mode); flushed after each `next_batch`. Zero cost to model
calls.

### Static graph artifact — `<corpus>/explore_graph.json`

Exported once per corpus, reused across runs:

- **nodes**: `{id, kind (corpus / cited), title, pagerank, is_corpus}`
  for all source nodes; `{id, doc_id, section, is_caption}` for chunks.
- **edges**: `{src, dst, type}` for `cites`, `doc_similar`, `co_section`,
  `authored_by` (typed — requires Issue 2 fixed).
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
| Issue 2: edge-type serialization | ~10 LOC |
| Issue 5: caption gating | ~20 LOC |
| Issue 3: `refine_uncertain` (wire or remove) | ~20 LOC |
| Issue 4: bootstrap (implement abstract sweep) | ~30 LOC |
| Static panels script | ~200 LOC |
| Animated video script | ~150 LOC |
| Smoke run + Playbook Part 5 review | half-day |

## Adjacent improvements — ergonomics and Obsidian overlay

Patterns to adopt from LLM-wiki practice, filtered through wikify's
constraints: evidence-first pages, python pipeline, CLI-driven, study
output as the primary product, "no wikilinks in body prose" rule, the
vault-first pivot that already pushes chat / vector / graph into
Obsidian plugins.

None of these touch the scientific core (vectors, KG, metrics, HTML
renderer, cost meter, strategies). They add human-review ergonomics on
top of existing artifacts.

### Patterns (ordered by effort × value)

1. **`hot.md` per bundle and per corpus.** ~500-word human-readable
   markdown. For a bundle: last iteration's strategy / mode / budget,
   M1 and M6 status, top-10 open coverage gaps from
   `coverage_memory.json`, last pages touched, current
   `write_rejections`. For a corpus: last ingest's doc-count delta,
   unresolved-DOI count, `is_junk_title` warnings, embedder
   fingerprint. Structured H2 headings so the next LLM session can
   parse them. Lives next to `_run.json` / `coverage_memory.json` /
   `manifest.json`, does NOT replace them.

2. **`runs.md` per bundle.** Markdown render of
   `_meta/run_history.jsonl`. One line per iteration, columns:
   timestamp, strategy, mode, budget_used / target, M1, M3, grounding
   pass, n_pages, n_rejections. Pure presentation layer.

3. **Cross-bundle `comparison.md` for study outputs.** When
   `wikify study` finishes, emit `<out_dir>/comparison.md` putting
   every preset × budget × seed cell side-by-side with M1 / M2 / M3
   / M5 / R_P / R_C_declared / G1 / G2. The reviewer-facing surface
   of a study run.

4. **Writer callouts in the style guide.** Four Obsidian-flavoured
   callouts, mapped to wikify's evidence-first semantics:
   - `> [!contradiction]` — two Evidence items on the same dossier
     disagree.
   - `> [!gap]` — a section the artifact template expects has no
     dossier backing (currently the writer produces vague prose;
     callout makes the gap explicit).
   - `> [!key-insight]` — a quantified claim grounded in evidence
     from ≥2 docs.
   - `> [!stale]` — a claim whose only citing doc is more than N
     years older than the most recent doc on the same topic in the
     corpus. Auto-inserted by a post-write pass.

   Does not conflict with the "no wikilinks in body prose" rule:
   callouts are prose, not wikilinks.

5. **Bidirectional contradiction detection at canonicalize.** Concrete
   wikify hook: `distill/dossier.py::canonicalize` already merges
   candidates by normalised title / alias. Extend it: when two merged
   candidates' `(definition, summary)` pairs disagree beyond a
   threshold (embedding cosine < 0.6 on the pair, or a tier-S
   tiebreak call), tag both dossier entries with a `conflict_with`
   id. The writer reads these tags and emits `[!contradiction]` on
   the resulting page.

6. **`wikify lint --bundle` command.** One entrypoint consolidating
   playbook Parts 5, 6, 9 into a markdown report at
   `_meta/lint-report-YYYY-MM-DD.md`. Wikify-specific categories:
   - pages with `evidence_count = 0` (skeletons leaked past filter)
   - `[^eN]` markers that don't resolve (overlaps M6 G2)
   - `_index.md` entries pointing at renamed / deleted pages
   - pages missing required frontmatter (`title`, `kind`, `created`)
   - H2 sections the artifact template declared but left empty
   - callout counts (`[!gap]`, `[!contradiction]`, `[!stale]`)
   - frontmatter `links:` targets that don't resolve.

   Exit non-zero if any category is above threshold. Replaces a chunk
   of the manual review workflow.

7. **Explicit round semantics for `refine`.** Campaign already does
   this implicitly via `coverage_gap`. Document the mapping in
   `study-design.md`:
   - Round 1 (`iteration=create`): broad, bootstrap + global_op heavy.
   - Round 2 (`iteration=refine`, adaptive): gap-fill, coverage_gap
     on the top-residual residual set, evidence-anchored similarity
     walks.
   - Round 3 (`iteration=refine` second pass OR `iteration=merge`):
     synthesis — cross-link pass + callout resolution.
   Matches Karpathy autoresearch's broad → gap-fill → synthesis
   structure.

8. **Per-page write-with-revise subagent.** Not about parallelism —
   parallelism for single-call writes belongs at the dispatch layer
   (async `_dispatch_many`, not subagent spawn). Motivation is
   **multi-turn quality per page**: today `_run_write_pass` catches
   `ValidationError`, appends to `write_rejections`, and moves on
   without retrying. A self-contained per-page subagent runs
   `write → validate → if ValidationError, revise once at tier+1 →
   emit final or .error.json`. Isolated context, own turn budget.
   Reduces rejection count; plays well with staged dispatch.

9. **Bundle README with cross-project snippet.** `wikify html` (or a
   new `wikify readme`) emits `<bundle>/README.md` that includes a
   paragraph telling an external Claude Code project how to read this
   bundle from another directory (`hot.md → index.md → page`). Makes
   wikify outputs reusable as a knowledge base for adjacent projects.

10. **`/wikify` router skill (thin).** One Claude Code skill
    dispatching `ingest X`, `distill X`, `lint X`, `query X`, `study X`
    to the corresponding CLI commands. Wikify is CLI-first, so this is
    a veneer for interactive sessions — not a second source of truth.
    Skill stays short; all logic remains in the CLI.

### Obsidian overlay — dual-vault (corpus + wiki)

Goal: make both the ingested corpus and the distilled wiki navigable
as Obsidian vaults **in addition to** the existing HTML renderer and
scientific tooling. The Wikipedia-style HTML stays as-is; it is the
canonical reader surface for a chosen bundle. The Obsidian layer is a
sibling surface: useful for exploration, cross-linking, and lint, not
for publication.

#### Wiki bundle (additive)

Keep `_html/`, `articles/`, `people/`, `_run.json`, `_meta/` exactly
where they are. Add at the bundle root:

- `index.md` — rename `_index.md` → `index.md` (no dead versioning;
  delete the old). Obsidian picks it up as the landing page.
- `hot.md`, `runs.md` — as described above.
- `_templates/{article,person}.md` — Obsidian Templater templates.
- `.obsidian/snippets/wikify-callouts.css` — four callout styles.
- `_attachments/images/` — symlink (or copy) of corpus figures that
  appear on any wiki page so Obsidian resolves `![[fig]]` embeds
  inside the vault.

Frontmatter additions on every page:
- `status: skeleton | draft | published`
- `bibkeys: [...]` — derived from Evidence records
- `evidence_count: N`
- `gap_count`, `contradiction_count` — populated by `wikify lint`

**No wikilinks in body prose.** Graph edges come from two places:
- the existing `links:` frontmatter field (already emitted by the
  crosslink pass) — Obsidian reads frontmatter links when configured
  via Graph settings;
- an auto-generated "See also" section appended at the end of every
  page by a post-write pass, containing wikilinks derived from
  `links:`. Wikipedia pattern — end-of-article, not inline.

Staging dirs (`_dossiers/`, `_write_requests/`, `_meta/`) stay
underscore-prefixed. Hide them via Obsidian's `app.json`
(`userIgnoreFilters`) rather than renaming — renaming has too large a
blast radius across the Python pipeline.

#### Corpus (additive)

Corpus markdown at `corpus/markdown/{doc_id}.md` is already
Obsidian-readable. Keep `vectors.npz`, `knowledge_graph.json`,
`manifest.json`, `chunks/`, `docs/` untouched. Add an overlay on top:

- `corpus/index.md` — doc catalogue grouped by year / author / venue,
  one row per doc with `[[markdown/doc_id|Title]]` link, bibkey,
  chunk count, figure count.
- `corpus/hot.md` — last ingest summary: doc-count delta,
  unresolved-DOI count, junk-title warnings, embedder fingerprint.
- `corpus/log.md` — ingest history rendered from `manifest.json`.
- `corpus/entities/{author_slug}.md` — one page per author, derived
  from KG via `build_author_context`: papers authored, co-authors,
  year range, affiliations.
- `corpus/concepts/{topic_slug}.md` — one page per sanitised topic in
  `topics.json`: document frequency, linked docs, declared vs.
  inferred flag.
- `corpus/figures/index.md` — caption-indexed catalogue linking to
  binary figures in `images/`.
- `corpus/.obsidian/snippets/corpus-callouts.css` — same callouts.

All derived, not source-of-truth. Regenerated at the end of `ingest`
(new `build_obsidian_overlay` step after step 6 in the ingest
pipeline). Hide `vectors.*`, `.citestore.db`, `manifest.json`,
`chunks/`, `docs/` via Obsidian's `app.json`.

#### Cross-vault link (what makes the dual-vault actually useful)

Every wiki page's evidence footnote references `chunk_id = "doc5#c12"`.
A post-render pass appends an Obsidian wikilink next to each
footnote body targeting `../corpus/markdown/doc5` — so the reviewer
can jump from a wiki claim straight to the source corpus markdown in
one hop, inside the same Obsidian window. This is the piece that
turns two separate vaults into one navigable system.

#### What this does NOT do

- Does not replace the Wikipedia-style HTML renderer.
- Does not change the distill pipeline, the strategies, or the
  metrics framework.
- Does not put wikilinks into body prose (Wikipedia-voice rule holds).
- Does not version Python paths to look Obsidian-native — underscore
  staging dirs stay, Obsidian is configured to hide them.

#### Effort

| Piece | Rough effort |
|---|---|
| `hot.md` + `runs.md` renderer (bundle + corpus) | ~100 LOC |
| `comparison.md` for `wikify study` | ~80 LOC |
| Four callouts in style guide + artifact template + CSS | ~40 LOC + 40 LOC CSS |
| Bidirectional contradiction at canonicalize + writer prompt hook | ~80 LOC |
| `wikify lint --bundle` command | ~250 LOC |
| Bundle `_attachments/` symlink pass + See-also appender | ~80 LOC |
| Corpus `index.md` / `hot.md` / `log.md` | ~120 LOC |
| Corpus `entities/` + `concepts/` + `figures/` derivations | ~180 LOC |
| Cross-vault footnote wikilink post-render pass | ~60 LOC |
| Frontmatter additions (`status`, `bibkeys`, counts) | ~40 LOC |
| Per-page write-with-revise subagent + pipeline hook | ~120 LOC |
| `/wikify` router skill | ~60 LOC skill file |
| `wikify readme` command (or fold into `html`) | ~40 LOC |

Total ~1300 LOC, no changes to the scientific core.

### Explicitly not adopted

- PostToolUse auto-commit of every wiki edit — noisy history, rejected.
- Replacing the Wikipedia-style HTML with an Obsidian-only surface —
  Obsidian is additive.
- `hot.md` / `runs.md` as the sole state carrier — the structured
  JSONs stay authoritative for reproducibility.
- Spawning a subagent per page just to parallelise single-call writes
  — parallelism belongs at the dispatch layer. Subagent isolation is
  reserved for multi-turn per-page logic (write-with-revise).
- Wikilinks in body prose — conflicts with the Wikipedia-voice rule.
  Graph edges come from frontmatter + a See-also section.

## Open decisions

1. **Ablation scope for first run.** 5 anchor cells at 3 budgets × 3
   seeds = 45 runs vs. full 33-cell ablation (+66 runs).
2. **Baseline.** Include `baselines/pipeline.py` in the first study or
   defer.
3. **PageRank fix direction.** (a) `cites ∪ doc_similar`,
   (b) personalised PageRank on corpus, (c) accept flat and document.
   Lean (a).
4. **Replay viewer target.** Static panels only, GIF/mp4 as well, or
   interactive HTML on top.
5. **Bootstrap.** Implement abstract sweep, or pin current and update
   `docs/strategies.md`.
6. **Obsidian overlay rollout.** Ship all 9 patterns at once, or start
   with the cheap three (`hot.md`, `runs.md`, callouts) and gate the
   rest on reviewer feedback.
