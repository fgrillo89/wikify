# Study design: model autonomy over corpus navigation

## Goal

Compare how much control to give the model over corpus exploration and
context management when building encyclopedic wikis from academic papers.

Evidence is always on (full provenance). The independent variable is
**exploration autonomy**: from zero (baseline RAG) through rule-based
(scripted sampler) to model-driven (interactive KG tool-calling).

## Two modes, one parametric

### Baseline mode

Retrieve-and-summarise. No iterative exploration.

1. **Topic discovery**: one LLM call over KG source summaries to produce
   a topic vocabulary and key authors.
2. **Parallel retrieve-and-write**: per topic, `kg.search(topic,
   top_k=20)` -> chunk texts -> single writer call with full evidence
   prompt. Per author, `kg.sources(author=name)` -> writer call.
3. **Post-hoc citation**: doc-level citation pass for ungrounded claims.
4. **Refinement** (rounds 2+): re-retrieve for weak pages, expand topic
   vocabulary from discovered concepts.

Same prompt stack as the normal pipeline (field guide + artifact template
+ style guide) so output format is comparable.

```bash
wikify study --presets balanced --include-baseline --budgets 1x --seeds 0
```

### Normal mode (parametric)

The standard wikify pipeline: explore corpus -> extract concepts ->
write pages. The key parameters control how much autonomy the model has:

| Parameter | Range | Effect |
|-----------|-------|--------|
| `--mode` | `scripted` / `guided` | Who decides what to explore next |
| `--strategy` | `E` / `M` / `X` / custom | Sampler parameters (local_op, global_op, jump_rate) |
| `--guided-tools` | `navigate` / `full` | What tools the orchestrator gets (A2 vs A3) |
| `--budget` | haiku-eq | Total budget for the run |

In **scripted** mode, the LevyExplorer drives all exploration decisions
deterministically. In **guided** mode, the orchestrator is a
tool-calling agent with interactive KG access.

The `--guided-tools` flag controls the orchestrator's autonomy:

- `navigate`: model picks what to explore (KG queries + `sample_chunks`
  + `write_now`) but the harness manages budget allocation. Stopping is
  algorithmic (AdaptiveBudget).
- `full`: model also controls stopping (`done`) and budget allocation
  (`set_allocation`). Maximum autonomy.

```bash
# Fully deterministic (old A1)
wikify distill --mode scripted --strategy M --budget 1x

# Model navigates, harness manages budget (old A2)
wikify distill --mode guided --guided-tools navigate --budget 1x

# Full model autonomy (old A3)
wikify distill --mode guided --guided-tools full --budget 1x
```

### Named presets

Stored in `src/wikify/distill/strategy.py` as `PRESET_CONFIGS`. The
small-scale run uses the canonical three:

| Preset | Mode | Strategy | Guided tools | Budget allocator |
|--------|------|----------|--------------|------------------|
| `baseline` | baseline pipeline (`baselines/pipeline.py`) | balanced (tiers + split only) | -- | Static 35% exploit (60/35/5 split, 1/3 abstract seeding + 2/3 evidence retrieval) |
| `balanced` | scripted | balanced (similarity_walk + coverage_gap, jump_rate=0.1) | -- | Static 35% exploit (60/35/5 split) |
| `guided` | guided | balanced (fallback) | full | Static 35% exploit; orchestrator may re-time via `set_allocation` and `write_now` |

The legacy preset aliases (`scripted-mixed`, `guided-full`,
`scripted-explore`, `scripted-exploit`, `guided-navigate`) and the
legacy `E` / `M` / `X` strategy ids have been removed. See
`docs/distill-test-readiness.md` for the historical migration map.

Presets are convenience shortcuts. All parameters can be overridden:

```bash
# Use a canonical preset
wikify distill --preset guided --budget 1x

# Override tiers on a preset
wikify distill --preset balanced --extract-tier M --budget 1x
```

## Budget and convergence

### Total budget as the invariant

Each run (baseline or normal) gets the same total budget. Quality vs
tokens-spent convergence curves are the primary study output.

### Quality-gated convergence loop

```python
sub_budget = total_budget / max_rounds
prev_metrics = None

for round in range(max_rounds):
    if budget_remaining < min_useful_budget:
        break
    run_one_round(sub_budget)
    metrics = eval(bundle)  # M1-M6 + grounding
    if prev_metrics and metrics_delta(metrics, prev_metrics) < threshold:
        break  # converged
    prev_metrics = metrics
```

`min_useful_budget`: ~5k haiku-eq (one extract+write cycle).

`metrics_delta`: weighted combination of coverage (M1), grounding
(G1/G2), and page completeness. Weights TBD from pilot runs.

### Within each round

**Baseline**: discover/refine topics -> retrieve -> write. Single pass.

**Scripted**: standard pipeline. Round 1 = create. Round 2+ = refine
with coverage memory from disk.

**Guided**: continuous orchestrator session for the sub-budget duration.
Model interleaves explore and write freely. Coverage state stays live.
At round boundaries, coverage memory saved for checkpointing.

## Guided mode: interactive KG tool-calling

### Architecture

The orchestrator is a tool-calling agent. Each step is a multi-turn
conversation:

```
orchestrator receives: state summary + tool definitions
tool loop:
  1. model calls search_chunks("memristor switching") -> 10 results
  2. model calls get_citations("paper_A", "cited_by") -> 5 source_ids
  3. model calls search_chunks("reliability", source_id="paper_B") -> 8
  4. model calls sample_chunks(["chunk_1", "chunk_5", "chunk_9"]) -> END
harness extracts chunks 1, 5, 9 (costs budget)
```

### KG tools

| Tool | Args | Returns | Cost |
|------|------|---------|------|
| `search_chunks` | query, top_k, source_id? | chunk summaries | Free |
| `get_source_info` | source_id | source metadata + stats | Free |
| `list_sources` | sort_by?, limit? | source summaries | Free |
| `get_citations` | source_id, direction | source_ids | Free |
| `get_coverage` | -- | coverage state + histogram | Free |
| `get_pages` | -- | page summaries | Free |
| `get_budget` | -- | spent/total/remaining | Free |
| `sample_chunks` | chunk_ids | ack | **Terminal** |
| `write_now` | -- | ack | **Terminal** |
| `done` | -- | ack | **full tools only** |
| `set_allocation` | exploit_fraction | ack | **full tools only** |

KG tools execute locally (no LLM call). Only cost is the orchestrator
call itself (~30k haiku-eq at tier L). Terminal actions end the tool
loop and return control to the pipeline.

### Continuous session flow

```python
while budget_remaining_for_round > 0:
    decision = orchestrator.step(state, tools=allowed_tools)

    if decision.action == "sample_chunks":
        for chunk_id in decision.chunk_ids:
            extract(chunk_id)
            update_coverage(chunk_id)
        candidates.extend(canonicalize_incremental(new_extractions))

    elif decision.action == "write_now":
        pages = canonicalize(candidates, existing=existing_pages)
        build_dossiers(pages, candidates)
        compact_dossiers(pages)
        write_pages(pages)

    elif decision.action == "done":  # full tools only
        write_remaining_pages()
        break

save_coverage_memory(state)
```

### Tool filtering

```python
NAVIGATE_TOOLS = frozenset({
    "search_chunks", "get_source_info", "list_sources",
    "get_citations", "get_coverage", "get_pages",
    "get_budget", "sample_chunks", "write_now",
})
FULL_TOOLS = NAVIGATE_TOOLS | {"done", "set_allocation"}
```

### Dispatch protocol

Multi-turn file-based dispatch for tool-calling:

```
1. Write orchestrate.request.json (state + tool_definitions)
2. Await orchestrate.response.json
   - If tool_use blocks: execute locally, write tool_results.json, await next response
   - Repeat until no tool_use
   - Final response parsed as OrchAction
```

## Study CLI

```bash
wikify study \
  --presets baseline,balanced,guided \
  --include-baseline \
  --budgets 3x \
  --seeds 0,1,2 \
  --max-rounds 3 \
  --convergence-threshold 0.02
```

## Implementation plan

### Files to modify

| File | Change |
|------|--------|
| `distill/strategy.py` | `allowed_tools` on GuidedMode, tool constants, `write_now` action, presets, continuous session |
| `distill/explorer.py` | Enrich `build_snapshot` (budget, novelty, residual histogram, pages) |
| `distill/pipeline.py` | Remove evidence_mode. Continuous-session for guided. Interleaved extract+write. |
| `dispatch.py` | `_dispatch_model_with_tools()` for multi-turn orchestrator |
| `schema.py` | Remove evidence_mode from WriteRequest. Extend OrchState. Tool schemas. |
| `distill/write_prep.py` | Remove evidence_mode filtering |
| `cli.py` | `--preset`, `--guided-tools`, convergence-loop `study`, `baseline` command |
| `baselines/pipeline.py` | Consolidated baseline: LLM topic discovery + parallel write + post-hoc cite |
| `types.py` | Update Orchestrator protocol for tool-calling |

### What stays unchanged

- LevyExplorer and operator dispatch
- ExplorerState and coverage feedback
- KnowledgeGraph and fluent API
- Writer, extractor, compactor, editor protocols
- Metrics framework (M1-M6, grounding)
- Trace and verbalize infrastructure
- CostMeter

### Verification

1. `uv run ruff check src/wikify`
2. `uv run pytest tests/wikify -q`
3. Grep for evidence_mode -- verify clean
4. Unit test: tool-calling dispatch with mock orchestrator
5. Unit test: write_now mid-session (empty + non-empty candidates)
6. Unit test: convergence detection
7. Smoke: `wikify distill --preset balanced --budget 0.1x --seed 0`
8. Smoke: `wikify study --presets baseline,balanced,guided --budgets 0.1x --seeds 0`
