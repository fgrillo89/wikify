# wikify_simple -- operator runbook

> Before running any test campaign: read [`test-run-playbook.md`](test-run-playbook.md) for the full setup-run-review procedure with an explicit quality-review checklist. This runbook covers the per-command reference.

## CLI reference (distill)

| Flag | Values | Default | Notes |
|---|---|---|---|
| `--strategy` | `E` / `M` / `X` | required | Strategy preset (sampler + schedule + default tiers). |
| `--policy` | `rule_policy` / `llm_policy` | `rule_policy` | `llm_policy` requires `--binding file_dispatch`. |
| `--binding` | `fake` / `heuristic` / `file_dispatch` | `fake` | `file_dispatch` requires `WIKIFY_SIMPLE_ALLOW_NETWORK=1`. |
| `--budget` | integer, `Nk`, `NM`, or `0.1x`/`1x`/`3x` | `1x` | Haiku-equivalent tokens. Shortcuts: `0.1x=5k`, `1x=50k`, `3x=150k`. |
| `--extract-tier` | `S` / `M` / `L` | strategy default | Override the extract-call tier. |
| `--write-tier` | `S` / `M` / `L` | strategy default | Override the writer tier. |
| `--edit-tier` | `S` / `M` / `L` | strategy default | Override the editor tier. |
| `--compact-tier` | `S` / `M` / `L` | strategy default | Override the compactor tier. |
| `--exploit-fraction` | `0.0`-`1.0` | strategy default | Share of budget reserved for writes. |
| `--iteration` | `create` / `refine` / `merge` | `create` | Semantic op (per-iteration, not a loop count). |
| `--bundle` | path | — | Pin the bundle path across iterations. Without it, `create` writes to a timestamped subdir and `refine` writes to the parent, which is a known footgun. **Use `--bundle` for any multi-iteration workflow.** |
| `--seed` | int | `0` | Increment per iteration for determinism. |
| `--corpus` | path | `data/corpus` | Ingested corpus directory. |
| `--out` | path | `data/wikis` | Fallback when `--bundle` is not set. |
| `--phase` | `all` / `extract` / `write` | `all` | Phase gate. |
| `--field` | field-guide name | auto-detect | `materials_science`, `biology`, ... |
| `--artifact` | artifact template | `wiki_article` | One of `wiki_article`, `wiki_person`. |
| `--verbalize` | bool | `false` | Ad-hoc diagnostic flag. When set, every handler emits a 1-3 sentence `reasoning` field and the pipeline appends non-empty entries to `<bundle>/_meta/verbalize.jsonl`. See `test-run-playbook.md` Part 8. |

**Orchestrate tier is locked at L (opus)** — not exposed as a flag. See `contracts/roles.py`.

## Quick start

### Fast smoke test (heuristic binding, no models, ~5s per iteration)

```bash
WIKIFY_SIMPLE_EMBEDDER=sentence_transformers uv run python -m wikify_simple.cli distill \
  --strategy M --binding heuristic --budget 50000 --seed 0 --iteration create \
  --corpus data/wikify_simple/corpora/mvp20_v6 \
  --bundle data/wikify_simple/test_runs/smoke
uv run python -m wikify_simple.cli html --bundle data/wikify_simple/test_runs/smoke
uv run python -m wikify_simple.cli eval --bundle data/wikify_simple/test_runs/smoke --corpus data/wikify_simple/corpora/mvp20_v6
```

Heuristic is in-process regex; useful for pipeline sanity but produces no real prose. For quality runs use `file_dispatch`.

### Model-backed scripted run (rule_policy + file_dispatch)

```bash
export WIKIFY_SIMPLE_ALLOW_NETWORK=1
export WIKIFY_SIMPLE_EMBEDDER=sentence_transformers

# Iteration 1 — create
uv run python -m wikify_simple.cli distill \
  --strategy M --policy rule_policy --binding file_dispatch \
  --budget 50000 --extract-tier S --write-tier M --exploit-fraction 0.65 \
  --seed 0 --iteration create \
  --corpus data/wikify_simple/corpora/mvp20_v6 \
  --bundle data/wikify_simple/test_runs/scripted

# Iteration 2 / 3 — refine (increment seed)
uv run python -m wikify_simple.cli distill \
  --strategy M --policy rule_policy --binding file_dispatch \
  --budget 50000 --extract-tier S --write-tier M --exploit-fraction 0.65 \
  --seed 1 --iteration refine \
  --corpus data/wikify_simple/corpora/mvp20_v6 \
  --bundle data/wikify_simple/test_runs/scripted
```

A parallel Claude Code session running `/wikify_simple/runtime/serve-dispatch` handles the `data/dispatch/` round-trips. See `test-run-playbook.md` for the full procedure.

### LLM campaign run (llm_policy + file_dispatch)

```bash
uv run python -m wikify_simple.cli distill \
  --strategy M --policy llm_policy --binding file_dispatch \
  --budget 200000 --seed 0 --iteration create \
  --corpus data/wikify_simple/corpora/mvp20_v6 \
  --bundle data/wikify_simple/test_runs/campaign
```

Each orchestrator decision costs ~30 k heq at tier L, so a realistic per-iteration budget floor is ~200 k. The LLM policy caches active sampling actions for up to 8 batches before re-querying the orchestrator.

### Pipeline validation (fake binding)

```bash
uv run python -m wikify_simple.cli distill \
  --strategy M --binding fake --budget 50000 --seed 0 --iteration create \
  --corpus data/wikify_simple/corpora/mvp20_v6 \
  --bundle data/wikify_simple/test_runs/fake
```

## Key files

| File | Purpose |
|------|---------|
| `distill/pipeline.py` | Main pipeline with phase support |
| `distill/extract/` | Extraction subpackage |
| `distill/write/` | Write subpackage |
| `distill/sampler.py` | LevyMixSampler (local/global/jump_rate) |
| `distill/schedule.py` | Budget split (static / adaptive) |
| `distill/iteration.py` | Create/refine/merge operations |
| `distill/policy.py` | Rule and LLM policy shared interface |
| `distill/strategies/` | E, M, X preset configurations |
| `contracts/schema.py` | All Pydantic schemas |
| `contracts/protocols.py` | Extractor, Compactor, Editor, Writer protocols |
| `contracts/roles.py` | Role enum + per-role spec lists |
| `contracts/normalize.py` | Text normalization for quote validation |
| `bindings/heuristic.py` | Inline regex extraction + article assembly |
| `bindings/fake.py` | Deterministic fakes for testing |
| `bindings/file_dispatch.py` | File-dispatch bindings (staged, slow) |
| `store/corpus_profile.py` | PageRank, Louvain, betweenness |
| `render/html/render.py` | HTML renderer |
| `prompts/*.yaml` | Prompt templates (`extract`, `write`, `compact`, `edit`, `query`) |

## Sampler parameter space

The sampler is a Levy flight with three knobs:

| Knob | Values | Effect |
|------|--------|--------|
| `local_op` | similarity_walk, refine_uncertain, none | How to explore nearby chunks |
| `global_op` | uniform, pagerank, coverage_gap | Where to jump |
| `jump_rate` | 0.0 -- 1.0 | Probability of global jump vs local walk |

Each step: flip coin with P=jump_rate. Heads -> global jump (pick a doc,
return its top chunks). Tails -> local walk (follow similarity edges from
an existing wiki chunk). Bootstrap forces global jumps until the wiki has
content to walk from. Fallback to global when local walk is exhausted.

The three presets:

| Strategy | local | global | jump_rate | schedule | tiers |
|----------|-------|--------|-----------|----------|-------|
| E | none | pagerank | 1.0 | static 20% write | S/S |
| M | similarity_walk | coverage_gap | 0.1 | adaptive 65% write | S/M |
| X | similarity_walk | uniform | 0.0 | static 60% write | M/M |

## Pre-flight (file_dispatch binding)

### 1. Environment variables

```bash
export WIKIFY_SIMPLE_EMBEDDER=sentence_transformers
export WIKIFY_SIMPLE_DISPATCH_DIR=data/dispatch        # default
```

### 2. Model + budget

| knob | recommended for mvp20 | why |
|---|---|---|
| `--model` | `haiku` | extractor is the hot loop; haiku keeps the run cheap |
| `--strategy` | `mixed` | exploit/explore split, the default for first runs |
| `--budget` | `300000` haiku-eq | enough for ~700 extract calls + ~150 write calls + headroom |

Token estimate (mvp20, 689 chunks, <=208 pages):

| call | tier | tokens in / out | per-call heq |
|---|---|---|---|
| extract (no images) | S | 250 / 120 | ~420 |
| extract (+10 images) | S | 650 / 120 | ~820 |
| write (no figures) | M | 300 / 120 | ~5,600 |
| write (+14 figures) | M | 1000 / 120 | ~14,000 |

### 3. Skills

The outer Claude Code session needs skill files at
`.claude/skills/wikify_simple/`:

- `/wikify_simple/extract` -- reads `data/dispatch/extract/*.request.json`,
  emits an `ExtractResponse`-shaped JSON to the matching `.response.json`.
- `/wikify_simple/write` -- same shape for `WriteResponse`.
- `/wikify_simple/query` -- same shape for `QueryResponse`.

Skills must match the schemas in `contracts/schema.py` (Pydantic v2,
`extra="forbid"` -- any extra field rejects the response).

## Step-by-step (file_dispatch binding)

### 1. Ingest

```bash
rm -rf data/wikify_simple/corpora/mvp20_real
uv run python -m wikify_simple.cli ingest \
  --source data/papers/mvp20 \
  --corpus data/wikify_simple/corpora/mvp20_real
```

### 2. Distill

```bash
uv run python -m wikify_simple.cli distill \
  --corpus data/wikify_simple/corpora/mvp20_real \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --strategy mixed \
  --binding file_dispatch \
  --budget 300000 \
  --model haiku \
  --seed 0
```

What happens during the run:

1. The harness loads the corpus, vectors, graph, image index, and sampler.
2. For each sampled chunk it writes
   `data/dispatch/extract/<rid>.request.json` and blocks polling for
   `data/dispatch/extract/<rid>.response.json` (timeout 600s, poll 0.25s).
3. The outer Claude Code session writes the response (auto-hook or manual).
4. Python validates the response against `ExtractResponse`, charges the
   cost meter, optionally writes to `ExtractCache`, and moves on.
5. After the extract budget is spent, the harness canonicalises candidates,
   then runs the writer loop with the same request/response dance.
6. Crosslink + page write + index rebuild + run snapshot.

### 3. Inspect mid-run

```bash
watch -n 5 'cat data/wikify_simple/wikis/mvp20_real_M/M_*/_run.json | python -m json.tool | head -40'
```

Useful fields: `budget_used_haiku_eq`, `by_role.{extractor,writer}.calls`,
`by_role.*.cache_hit_rate`, `by_role.*.headroom_min`.

### 4. Abort cleanly

`Ctrl-C` in the harness terminal. Clean up dispatch files:

```bash
rm -f data/dispatch/extract/*.request.json data/dispatch/extract/*.response.json
rm -f data/dispatch/write/*.request.json data/dispatch/write/*.response.json
```

The cache (`data/cache/extract/`) survives. Re-running picks up where
the budget left off. Pass `--feed` to merge against an existing bundle.

Cache entries are namespaced by binding under
`data/cache/extract/<binding_name>/<model_id>/<prompt_hash>/<chunk_hash>.json`.

## Post-run

### 1. Eval

```bash
uv run python -m wikify_simple.cli eval \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --corpus data/wikify_simple/corpora/mvp20_real
```

Writes `_metrics.md` + `_metrics.json` next to the bundle.

### 2. Query

```bash
uv run python -m wikify_simple.cli query \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --binding file_dispatch \
  "what is a memristor?"
```

Result file lands at `data/queries/<bundle_name>/<utc_iso_compact>.md`.
The bundle is never mutated by a query call.

## Troubleshooting

### Dispatcher hang

`TimeoutError: no response at <path>` after 600s. Check:

1. Is the skill enabled in the outer Claude Code session?
2. Does the request file exist? (`ls data/dispatch/extract/`)
3. Did the skill emit a `.response.json` next to the `.request.json`?
4. Are the paths in the same directory? The binding strips the request
   file's `.request.` segment to derive the response path.

### Schema validation failure

`pydantic_core._pydantic_core.ValidationError`. The response file has a
shape the model rejects. Schemas are strict (`extra="forbid"`), so any
unexpected key fails. Read `contracts/schema.py` for the canonical shape.

### Budget exhaustion mid-write

The extractor over-consumed and the writer ran out. Re-run with a higher
`--budget` or shift the extract/write split in the strategy schedule
(`distill/strategies/mixed.py`).

### Cache miss explosion

Every chunk is a cache miss. Likely the prompt template or model id
changed since the last run, invalidating every cache key. Verify
`prompt_hash` is stable across runs.

### MAX_PATH on Windows

Fixed in prior commits: `ExtractCacheKey.relpath` hashes the chunk_id and
the ingest image folder uses a word-bounded <=80-char slug. If you see a
fresh path-too-long error, check what new field is being written to disk.

## Scaling plan: 200-1000 papers

Target: 200-1000 papers, 12k-60k chunks, 200-500 concept pages.

### Step 1: Adjacency index for sampler (BLOCKER)

`_local_similarity_walk` scans ALL edges linearly on every walk step.
At 60k chunks with ~300k edges, this is 300M comparisons.

Fix: build `adjacency: dict[str, list[str]]` and `degree: dict[str, int]`
once in `_build_sampler_state()`. O(E) per step becomes O(degree).

Files: `distill/sampler.py`, `distill/pipeline.py`

### Step 2: God-node filtering in metrics

Hub concepts (degree > sqrt(n)) dominate PageRank and modularity at
scale. Detect them and compute metrics both with and without.

Files: `eval/metrics.py`

### Step 3: Richer audit report

Add god-node section, top-3 members per community, summary stats.

Files: `eval/audit.py`

### Step 4: Advisor-pattern escalation

Each agent (haiku/sonnet) can escalate to the editor (opus) when
uncertain. Escalation lives in the prompt, not in Python wrappers.

### What does NOT need changing at scale

- Vector store (numpy, fast at 60k)
- Corpus profiling (NetworkX, fast at 1000 docs)
- HTML renderer (I/O bound, acceptable)
- Crosslinking (fast string matching)
- Chunker (cheap per-chunk)
- Cache (60k JSON files, fine on local SSDs)

### Memory/time expectations

- Memory: ~10MB per 1000 chunks (vectors) + ~1MB per 1000 chunks (text)
- Sampler: fast after adjacency index (step 1)
- Metrics: M1 takes 10-30s at 60k chunks (once per run)
- HTML rendering: I/O bound, 2-8 min for 500 pages
- Advisor escalation rate: expect ~20% of requests to escalate

## Open items

### Strategy grid sweep

The meaningful grid is `jump_rate x global_op` with local_op fixed at
similarity_walk:

```
jump_rate:  [0.0, 0.1, 0.3, 0.5, 1.0]
global_op:  [uniform, pagerank, coverage_gap]
```

13 unique configs. Each takes ~3s with heuristic binding. Needs a sweep
harness that produces a comparison table of M1/M3/M5/G1 metrics.

### Model-backed extraction

Heuristic extraction finds concepts via regex but produces no definitions,
summaries, or parameters. Staged extraction with haiku subagents would
produce rich dossiers. Requires serializing ExtractRequests the same way
WriteRequests are serialized.

### Adaptive schedule tuning

The novelty threshold (0.05) and shift target (0.7) in AdaptiveSchedule
are untested. The grid sweep should include schedule variants.

### Corpus scaling

20 papers do not differentiate strategies. Need 50+ documents for
meaningful comparisons.
