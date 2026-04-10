# wikify_simple -- Handoff & Restart Guide

Last updated: 2026-04-10

## Architecture

Four-role pipeline with staged execution:

```
Extractor → Compactor → Editor → Writer
  (haiku)    (determ.)   (rule)   (sonnet subagents)
```

Three bindings control how each role runs:

| Binding | Speed | Quality | Model calls? |
|---------|-------|---------|--------------|
| `fake` | ~2s | placeholder tokens | no |
| `heuristic` | ~3s | domain-aware regex | no |
| `claude_code` | 6-10min | model-quality | yes (file dispatch) |

Staged execution (`--phase extract|write|all`) lets the orchestrator
process write requests between phases using subagents. This is the
recommended flow for model-quality output.

## Quick start

### Fast strategy test (~15s for all three strategies)
```bash
for S in E M X; do
  WIKIFY_SIMPLE_EMBEDDER=sentence_transformers uv run python -m wikify_simple.cli distill \
    --strategy $S --binding heuristic --budget 1x --seed 0 \
    --corpus data/wikify_simple/corpora/mvp20_v2 \
    --out data/wikify_simple/wikis/strategy_test
done
# Compare:
for d in data/wikify_simple/wikis/strategy_test/*/; do
  uv run python -m wikify_simple.cli eval --bundle "$d" \
    --corpus data/wikify_simple/corpora/mvp20_v2
done
```

### Model-quality run (staged pipeline, ~5 min)
```bash
# Phase 1: extract (heuristic, ~3s)
WIKIFY_SIMPLE_EMBEDDER=sentence_transformers uv run python -m wikify_simple.cli distill \
  --strategy M --binding heuristic --budget 1x --seed 0 \
  --corpus data/wikify_simple/corpora/mvp20_v2 \
  --out data/wikify_simple/wikis/mvp20_v3 \
  --phase extract

# Phase 2: process write requests with subagents (in Claude Code)
# The extract phase saves WriteRequest JSONs to <bundle>/_write_requests/
# Spawn haiku/sonnet subagents to read each .request.json, write the
# article, and save a .response.json in the same directory.

# Phase 3: assemble pages + crosslink + render
BUNDLE=data/wikify_simple/wikis/mvp20_v3/<run_dir>
uv run python -m wikify_simple.cli distill \
  --strategy M --binding heuristic --budget 1x --seed 0 \
  --corpus data/wikify_simple/corpora/mvp20_v2 \
  --out $BUNDLE --phase write
uv run python -m wikify_simple.cli html --bundle $BUNDLE --out $BUNDLE/_html
```

### Pipeline validation (fake binding)
```bash
WIKIFY_SIMPLE_EMBEDDER=sentence_transformers uv run python -m wikify_simple.cli distill \
  --strategy M --binding fake --budget 1x --seed 0 \
  --corpus data/wikify_simple/corpora/mvp20_v2 \
  --out data/wikify_simple/wikis/test_fake
```

## Key files

| File | Purpose |
|------|---------|
| `distill/pipeline.py` | Main pipeline with phase support |
| `distill/dossier.py` | Per-concept dossier model + persistence |
| `distill/sampler.py` | LevyMixSampler (local/global/jump_rate) |
| `distill/schedule.py` | Budget split (static / adaptive) |
| `distill/strategies/` | E, M, X preset configurations |
| `agents/schema.py` | All Pydantic schemas |
| `agents/protocols.py` | Extractor, Compactor, Editor, Writer protocols |
| `bindings/heuristic.py` | Inline regex extraction + article assembly |
| `bindings/fake.py` | Deterministic fakes for testing |
| `bindings/claude_code.py` | File-dispatch bindings (legacy, slow) |
| `store/corpus_profile.py` | PageRank, Louvain, betweenness |
| `render/html/render.py` | HTML renderer |
| `prompts/*.yaml` | Prompt templates (extract_v2, write_v2, etc.) |

## Sampler parameter space

The sampler is a Levy flight with three knobs:

| Knob | Values | Effect |
|------|--------|--------|
| `local_op` | similarity_walk, refine_uncertain, none | How to explore nearby chunks |
| `global_op` | uniform, pagerank, coverage_gap | Where to jump |
| `jump_rate` | 0.0 -- 1.0 | Probability of global jump vs local walk |

Each step: flip coin with P=jump_rate. Heads → global jump (pick a doc,
return its top chunks). Tails → local walk (follow similarity edges from
an existing wiki chunk). Bootstrap forces global jumps until the wiki has
content to walk from. Fallback to global when local walk is exhausted.

The three presets:

| Strategy | local | global | jump_rate | schedule | tiers |
|----------|-------|--------|-----------|----------|-------|
| E | none | pagerank | 1.0 | static 20% write | S/S |
| M | similarity_walk | coverage_gap | 0.1 | adaptive 65% write | S/M |
| X | similarity_walk | uniform | 0.0 | static 60% write | M/M |

## Open challenges

### 1. Model-backed extraction

The heuristic binding uses regex patterns for extraction. This produces
pages with the right structure but no definitions, summaries, or
parameters. Rich dossiers need model-based extraction via extract_v2.yaml.

**Next step:** use the staged pipeline for extraction too. Phase out of
extract → subagents process ExtractRequests with haiku → phase into build.
Requires adding ExtractRequest serialization to the extract phase (same
pattern as WriteRequest serialization).

### 2. Strategy grid sweep

The meaningful grid is `jump_rate x global_op` with local_op fixed at
similarity_walk:

```
jump_rate:  [0.0, 0.1, 0.3, 0.5, 1.0]
global_op:  [uniform, pagerank, coverage_gap]
```

13 unique configs (jump_rate=0.0 makes global_op irrelevant). Each takes
~3s with heuristic binding. Needs a sweep harness that runs all configs
and produces a comparison table of M1/M3/M5/G1 metrics.

### 3. Adaptive schedule tuning

AdaptiveSchedule shifts budget from extract to write when novelty drops
below a threshold (Heaps slope). The threshold (0.05) and the shift
target (0.7) are untested. The strategy grid should include schedule
variants.

### 4. Corpus size scaling

The 20-paper mvp20 corpus doesn't differentiate strategies well (all
produce 29-31 concepts). PageRank, communities, and coverage_gap need
50+ documents to show meaningful differences. Need a larger corpus.

### 5. Heuristic extraction is domain-specific

The regex patterns in `bindings/heuristic.py` cover memristor/ALD terms
plus generic academic methods. Other corpora need their patterns or
model-based extraction.

### 6. Dossier substance check

`has_substance` requires a definition or summary, which only model-based
extraction provides. Heuristic extraction always fails this check,
so the editor pass is a no-op with heuristic binding.

### 7. Port remaining parsers

`ingest/parsers/{pdf,docx,pptx,html}.py` are stubs. Only markdown
works. Real corpora need at least the PDF parser.

### 8. Figure embedding

Infrastructure exists (ImageIndex, ImageRef in WriteRequest, figure
fields in prompts) but no binding actually embeds figures in articles.
