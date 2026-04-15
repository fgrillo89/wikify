# Study design phase 2: implemented

All 4 deferred items from phase 1 are now implemented.
Phase 1 (#12) shipped: named presets, tool filtering, evidence_mode removal,
enriched snapshot, write_now action, CLI rework.
Phase 2: KG tools, multi-turn dispatch, continuous session, convergence,
baseline pipeline.

## 1. Multi-turn KG tool-calling dispatch

**Ref:** study-design.md, "Guided mode: interactive KG tool-calling"

The orchestrator is currently single-turn: it receives an OrchState snapshot
and returns one OrchAction. The design envisions a multi-turn tool-calling
loop where the orchestrator can issue free KG queries (search_chunks,
get_citations, get_source_info, list_sources, get_coverage, get_pages,
get_budget) before committing to a terminal action (sample_chunks, write_now,
done).

### What to build

- `dispatch.py`: Add `_dispatch_model_with_tools()` that implements the
  file-based multi-turn protocol:
  1. Write `orchestrate.request.json` with state + tool_definitions
  2. Await `orchestrate.response.json`
  3. If response contains tool_use blocks: execute KG queries locally,
     write `tool_results.json`, await next response
  4. Repeat until a terminal action (no tool_use)
  5. Parse final response as OrchAction

- KG tool implementations: pure-Python functions that query the
  KnowledgeGraph and return JSON-serializable results. No LLM call.
  Each tool maps to an existing KG fluent API method.

- `types.py`: Extend Orchestrator protocol to accept tool definitions
  (or keep protocol unchanged and handle tools inside dispatch).

- Tool schema definitions: JSON Schema for each tool's args/returns,
  included in the orchestrate request payload.

### Complexity

High. The file-based dispatch protocol needs a tool-loop extension.
The KG query implementations are straightforward (thin wrappers around
existing fluent API). The main risk is getting the multi-turn
request/response/tool_results cycle right in file-based dispatch.

---

## 2. Continuous guided session (interleaved explore+write)

**Ref:** study-design.md, "Continuous session flow"

Currently the pipeline has a clean extract-then-write separation. The
design wants guided mode to run continuously within a round's sub-budget,
interleaving explore and write freely. When the orchestrator says
`write_now`, the pipeline should:

1. Canonicalize current candidates into pages
2. Build dossiers, compact, write pages
3. Resume exploration with updated coverage state

### What to build

- `pipeline.py`: Restructure `run_with_preloaded` for guided mode to
  detect `decision.action == "write_now"` and trigger a mid-session
  write pass, then resume the extract loop.

- Coverage state must survive across write_now boundaries within a
  single round (it already persists across epochs, so the machinery
  exists).

- The write_now action is already wired in explorer.py (returns
  stop=True). The pipeline just needs to handle it differently from
  "done" — write and continue vs write and stop.

### Complexity

Medium. The pipeline loop needs refactoring but the primitives (
canonicalize, build_dossiers, write_pages, coverage state) all exist.

---

## 3. Quality-gated convergence loop

**Ref:** study-design.md, "Budget and convergence"

The study CLI currently runs single-round per condition. The design wants
a multi-round convergence loop:

```python
for round in range(max_rounds):
    run_one_round(sub_budget)
    metrics = eval(bundle)
    if metrics_delta(metrics, prev_metrics) < threshold:
        break
```

### What to build

- `cli.py` (study command): Add `--convergence-threshold` and
  `--max-rounds` parameters. Each preset x budget x seed condition
  runs up to max_rounds, checking convergence after each.

- `metrics_delta`: Weighted combination of coverage (M1), grounding
  (G1/G2), and page completeness. Requires the eval framework to be
  callable programmatically (currently it is via `eval` CLI command).

- Sub-budget splitting: `total_budget / max_rounds` per round, with
  `min_useful_budget` (~5k haiku-eq) early-stop.

### Complexity

Medium. The eval framework exists. The main work is wiring it into
the study loop and defining `metrics_delta` weights (TBD from pilots).

---

## 4. Consolidated baseline pipeline

**Ref:** study-design.md, "Baseline mode"

Current baselines (B1: retrieve-summarise, B2: post-hoc cite) work as
standalone functions. The design wants a consolidated baseline pipeline
with:

1. LLM-driven topic discovery (one call over KG source summaries)
2. Parallel retrieve-and-write per topic
3. Post-hoc citation pass
4. Refinement rounds (re-retrieve for weak pages)

### What to build

- `baselines/pipeline.py`: Orchestrate the full baseline flow.
  Replace static `topics.json` loading with LLM topic discovery.
  Use the same prompt stack as the normal pipeline for comparability.

### Complexity

Low-medium. The building blocks exist (B1 retrieval, B2 citation,
writer dispatch). Main addition is LLM topic discovery.
