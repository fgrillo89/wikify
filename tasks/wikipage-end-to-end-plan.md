# End-to-end wikipage artifact — plan

Status: proposed (2026-05-14)
Decisions captured below; live until executed or superseded.

## TL;DR

Baseline conventional RAG is already shipped end-to-end (citation gate
at write-time and re-checked at commit). The next step toward an
end-to-end wikipage artifact is **measurement, then graph-RAG additions
ablated against that measurement, with native graph viz built on the
same trace artifact that drives the ablation**. Ship in this order:

1. Trace + retrieval-eval instrumentation (Phase 1)
2. Baseline gauntlet on the current ALD Docling corpus (Phase 0,
   gated on Phase 1 so numbers are directly comparable later)
3. Graph viz MVP and graph-RAG additions in parallel (Phase 2 + 3a)
4. Re-run gauntlet with graph-RAG additions; compare (Phase 3c)
5. Guided mode on the new affordances; A/B vs scripted (Phase 4)
6. Defer figures/equations citations, reranker, summary tier
   (Phase 5)

## Decisions captured

- **Corpus**: current ALD Docling corpus under
  `data/corpora/ald_docling_*` (latest snapshot at run time).
- **Order**: Phase 1 first, then Phase 0. The gauntlet captures the
  new telemetry from the start so the first baseline numbers become a
  directly comparable anchor for every later ablation.
- **Stale memory entry** (`project_retrieval_strategies.md` claims 5
  strategies + hub-spoke that don't exist in code): leave for later.

## Phase 1 — Trace + retrieval-eval instrumentation

Two-for-one: prerequisite for measuring graph-RAG gains AND the data
source for the viz.

### 1a. Promote `TraceContext` to a stable artifact

Source: `src/wikify/corpus/graph.py:574` (`TraceContext`, append-only,
debug-only today).

Target: `<bundle>/run/retrieval.jsonl`, one JSON per CLI/MCP retrieval
op, schema:

```
{
  "step": int,                  // monotonic per run
  "tool": str,                  // corpus_find | corpus_show | ... | wiki_find | ...
  "args": object,               // call args, redacted
  "touched_ids": [str],         // chunk_id / doc_id / asset_id ...
  "latency_ms": int,
  "parent_step": int | null,    // for nested calls
  "narrative_hint": str,        // see 1b
  "set_hash": str               // sha1(sorted(touched_ids))
}
```

Stable schema so it survives ablation runs. Emitted by the same
boundary that already writes `cli_invoked` / `chunk_read` events into
`run/events.jsonl`.

### 1b. Derive `narrative_hint`

Compute per step from the delta of `touched_ids` vs the union of
previous steps:

- `hub_expansion`: this step's touched set is a strict superset of
  the previous step's set (drilled deeper into the same area)
- `lateral_jump`: zero overlap with previous step
- `backtrack`: non-empty intersection with a step >=2 back, but not
  with the immediately previous step
- `revisit`: identical `set_hash` to a prior step
- `terminal_write`: this step deposited evidence into a `work/`
  draft

This is the one transferable concept from memtrace-public; everything
else there is closed-source.

### 1c. M7 retrieval-recall@k metric

New metric next to the existing M5 hit-rate in `src/wikify/eval/metrics.py`:

```
M7 = | retrieved chunks ∩ chunks_used_as_evidence | / | chunks_used_as_evidence |
```

M5 measures retrieval-to-evidence funnel quality given a strategy;
M7 isolates retrieval recall before any selection. Together they
ablate retrieval changes from writing changes.

### 1d. Backfill scripted + guided skills

Both `wikify-baseline` and `wikify-guided-explore` skills
auto-inherit the new artifact (it lives at the corpus-CLI boundary,
not in skills). No skill edit needed in this phase.

### Exit criteria for Phase 1

- `retrieval.jsonl` written for every retrieval op in a smoke run
- M7 computed at end-of-run alongside M1..M6
- Schema documented in `docs/architecture.md` evidence-flow section
- Targeted tests under `tests/wikify/eval/` and
  `tests/wikify/corpus/`

## Phase 0 — Baseline gauntlet on the current ALD Docling corpus

Once Phase 1 lands.

- Pick latest `data/corpora/ald_docling_*`
- `wikify-baseline` skill, K=20 concepts, 4 parallel writers,
  claim TTL 1800 s
- Capture M1, M2, M3, M5, M6, M7 plus token cost (haiku-equivalent)
  and wall-clock into `eval/baselines/2026-05-XX.json` (date stamp at
  run time)
- Snapshot the per-page `retrieval.jsonl` so future ablations have a
  comparable trace set

This is the "before" picture. Without it, every graph-RAG ablation is
unmoored.

## Phase 2 — Graph viz MVP

Cytoscape.js, single self-contained HTML file per render. Three views:

```
src/wikify/viz/
  builder.py       # corpus_graph(), wiki_graph(), trace_graph()
  template.html    # cytoscape UMD inlined + JSON placeholder + UI shell
  render.py        # fills placeholder, writes single HTML to outdir
```

CLI: `wikify viz {corpus|wiki|trace} [--out path] [--compare scripted=<id> guided=<id>]`.

### Trace view (the novel one)

- Top scrubber bar: linear timeline, click any step to jump to it
- Play / Pause / Step controls; replay animates the build-up
- Active-step subgraph highlighted, rest dimmed
- Edges colored by step ordinal (viridis); revisited nodes get a
  count badge
- Header live counters: total steps, unique nodes touched, revisit
  rate, branching factor, longest citation chain, mean hop distance
- Linked views: click step -> isolate induced edges; click node ->
  filter step list to steps that touched it

### Compare mode

`--compare scripted=<id> guided=<id>` emits scripted vs guided
side-by-side in one HTML. Directly answers "is the model being
smart?" by visual diff of strategy shape.

### Stack rationale

- Cytoscape.js: single ~1 MB UMD bundle inlines cleanly; force /
  cose / dagre / breadthfirst layouts built-in; compound nodes
  (paper -> chunks); selectors; click/hover; canvas backend handles
  ~10k nodes
- Self-contained HTML: aligns with the project's "files are the
  interface" CLI philosophy; opens in Obsidian preview or any
  browser; no server, no build step
- Skipped: D3 (more code, larger artifact), Sigma.js (WebGL
  complicates single-file), Pyvis (dated API, fights time-axis
  needs), static SVG (interaction is the value)

## Phase 3 — Graph-RAG additions

Ordered by investment. 3a and 3b ship together; 3c is the
measurement; 3d and 3e are gated on what 3c reveals.

### 3a. Personalized PageRank + co-citation edges (L+L)

- PPR: `nx.pagerank(g, personalization={node: weight})` exposed as
  `corpus find --near-set <handles>`. Wraps next to
  `refresh_pagerank` in `src/wikify/corpus/store/metrics_global.py`.
- Co-citation edges: pure SQL join over
  `chunk_citations join bib_entries on target_doc_id`. New
  `graph_edges.kind = 'co_cited_with'`. New CLI relation:
  `traverse doc:X --to co-cited`.
- Audit `src/wikify/ingest/coupling.py` first; if it already builds
  bibliographic-coupling edges, wire it through instead of
  reimplementing.

### 3b. Leiden communities at ingest (L)

- Run Leiden on the `corpus_citation` view at
  `wikify corpus build`. `eval/community.py:louvain_communities`
  already exists; reuse.
- Persist `community_id` per node in `node_metrics`.
- New CLI ranker: `corpus find --by community` for
  diversity-aware seeding.
- No LLM summaries yet — just the partition.

### 3c. Re-run baseline gauntlet with 3a + 3b

- Same K=20 concepts, same writer tier, same claim TTL
- Compare every metric to Phase 0 baseline
- Open trace viz with `--compare baseline=<id> graph_rag=<id>` to
  see strategy shift visually

### 3d. LLM community summaries (M, gated on 3c)

Only ship if Phase 3c shows survey-kind pages bottlenecked on global
context.

- Generate summaries at one or two Leiden levels
- Persist `community_id -> chunk_ids` so the writer cites leaves,
  never summaries
- New retrieval verb `corpus find --community <id>`

### 3e. Per-concept lazy RAPTOR-lite (M, deferred)

- Per concept, cluster the ~50-200 chunks PPR returns; one summary
  call; use summary as lede-context, leaves as evidence
- Multi-granularity context per page without a corpus-wide tree
- Re-evaluate after 3a-3d data lands

### Skipped

- Full Microsoft GraphRAG indexing (cost-prohibitive at scientific-
  paper scale; global Leiden makes incremental ingest painful)
- Pre-computing similarity edges (vector search IS similarity; lesson
  already captured in `tasks/lessons.md`)

## Phase 4 — Guided mode with new affordances

- `wikify-guided-explore` skill picks up PPR + community verbs
  automatically (they're CLI verbs, the skill enumerates the catalog)
- Re-run gauntlet on the same 20 concepts in guided mode with the
  graph-RAG verbs available
- Compare:
  - Quality: M1, M2, M3, M5, M6, M7 vs scripted
  - Cost: tokens, wall-clock
  - Strategy: open `viz trace --compare scripted=... guided=...` and
    inspect branching factor, revisit rate, hop distance

## Phase 5 — Deferred

Worth doing later, taxes both schema and writer prompt:

- Figures and equations as first-class citable evidence
  (`[^fN]`, `[^xN]` markers; widen validator's substring grounding)
- Cross-encoder or LLM reranker between RRF and writer
- Summary-tier embeddings (paper-level abstracts and / or section-level
  rolled-up summaries as a sibling embedding space)

## Open questions (still pending decisions)

1. **Community detection**: ingest-time (cluster once at
   `corpus build`) vs lazy at first query? Friendlier-to-skills vs
   cheaper-on-ingest.
2. **Figure / equation citation markers**: introduce `[^fN]` /
   `[^xN]` (cleaner schema, more validator surface) vs keep them as
   structured side-context (current).
3. **Summary tier unit**: paper abstract (~10^3 docs), section
   summary (~10^4 sections), or community summary (~10^2 communities)?
4. **HippoRAG-style entity binding**: add a NER -> entity-node ingest
   pass, or stay chunk-as-atom? Skipping until 3a-3c numbers tell us
   it's a bottleneck.

## Immediate next ticket

Phase 1.1a + 1.1b + 1.1c: ship `run/retrieval.jsonl` artifact with
derived `narrative_hint`, plus M7 in `eval/metrics.py`, plus tests.
Land before kicking off the Phase 0 gauntlet.
