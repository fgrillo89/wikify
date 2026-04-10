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

## Scaling plan: 200-1000 papers

Target: 200-1000 papers, 12k-60k chunks, 200-500 concept pages.

### Step 1: Adjacency index for sampler (BLOCKER)

`_local_similarity_walk` (sampler.py:108) scans ALL edges linearly on
every walk step. At 60k chunks with ~300k edges and ~1000 walks per
iteration, this is 300M comparisons.

Fix: build `adjacency: dict[str, list[str]]` and `degree: dict[str, int]`
once in `_build_sampler_state()`. Rewrite walk/degree lookups. O(E) per
step becomes O(degree). Also fix `_doc_chunks_or_empty` (recomputes
degree from scratch) and `_global_coverage_gap` (sorts full dict).

Files: `distill/sampler.py`, `distill/pipeline.py`

Verify: 1017 tests pass. Run heuristic on mvp20_v2 -- same pages, faster.

### Step 2: God-node filtering in metrics

Hub concepts (degree > sqrt(n)) dominate PageRank and modularity at
scale. Detect them and compute metrics both with and without.

Files: `eval/metrics.py`

Verify: `_metrics.json` contains both `modularity` and `modularity_filtered`.

### Step 3: Richer audit report

Add god-node section, top-3 members per community, summary stats
(page count by kind, mean evidence count).

Files: `eval/audit.py`

Verify: `_audit.md` has "## God Nodes" section.

### Step 4: Advisor-pattern escalation

Each agent (haiku/sonnet) is instructed it can escalate to the editor
(opus) when uncertain. The editor is always top-tier. Escalation lives
in the prompt, not in Python wrappers.

**Extractor (haiku) escalates when:**
- Ambiguous terminology (could be concept or generic word)
- Conflicting evidence (chunk contradicts canonical_titles)
- Complex relationships (3+ concepts interacting)
- Novel concepts (important term not in canonical_titles)

**Writer (sonnet) escalates when:**
- Contradictory evidence across refs
- Cross-domain synthesis needed
- Insufficient evidence (<3 refs for 1200+ chars)
- Scope overlap with neighbor articles

**Neither escalates for:**
- Standard extraction/writing with clear evidence
- Person pages, bibliography, formatting
- Single-source evidence pages

Implementation: update prompt templates and skill files. The "escalation"
is the subagent spawning a nested opus subagent via the Agent tool. No
Python code changes needed.

Files: `prompts/extract_v2.yaml`, `prompts/write_v2.yaml`,
`.claude/skills/wikify_simple/extract.md`, `.claude/skills/wikify_simple/write.md`

### Step 5: Documentation

Add scaling section to this file with memory/time expectations:
- Memory: ~10MB per 1000 chunks (vectors) + ~1MB per 1000 chunks (text)
- Sampler: fast after adjacency index (step 1)
- Metrics: M1 takes 10-30s at 60k chunks (once per run, acceptable)
- HTML rendering: I/O bound, 2-8 min for 500 pages
- Advisor escalation rate: expect ~20% of requests to escalate

### What does NOT need changing

- Vector store (numpy, fast at 60k)
- Corpus profiling (NetworkX, fast at 1000 docs)
- HTML renderer (I/O bound, acceptable)
- Crosslinking (fast string matching)
- Chunker (cheap per-chunk)
- PDF parser (already complete)
- Cache (60k JSON files, fine on local SSDs)

## Open issues

### 1. Strategy grid sweep

The meaningful grid is `jump_rate x global_op` with local_op fixed at
similarity_walk:

```
jump_rate:  [0.0, 0.1, 0.3, 0.5, 1.0]
global_op:  [uniform, pagerank, coverage_gap]
```

13 unique configs. Each takes ~3s with heuristic binding. Needs a sweep
harness that produces a comparison table of M1/M3/M5/G1 metrics.

### 2. Adaptive schedule tuning

The novelty threshold (0.05) and shift target (0.7) in AdaptiveSchedule
are untested. The strategy grid should include schedule variants.

### 3. Heuristic extraction is domain-specific

The regex patterns in `bindings/heuristic.py` cover memristor/ALD terms
plus generic academic methods. Other corpora need model-based extraction.

### 4. Dossier substance check

`has_substance` requires a definition or summary, which only model-based
extraction provides. Heuristic extraction always fails this check.

### 5. Figure embedding

Infrastructure exists (ImageIndex, ImageRef in WriteRequest, figure
fields in prompts) but no binding actually embeds figures in articles.
