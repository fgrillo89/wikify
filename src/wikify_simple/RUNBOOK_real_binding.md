# Real-binding distill — operator runbook

The first run that *means anything* for `wikify_simple`. Until this is
executed against a real corpus with `--binding claude_code`, every
metric you see is a fake-binding artifact.

This runbook is for the human (and the outer Claude Code session) that
drives the dispatcher loop. The Python harness writes request files;
the outer session executes the matching skill and writes a response
file. Python never talks to a model directly.

## Pre-flight

### 1. Environment variables

```bash
export WIKIFY_SIMPLE_EMBEDDER=sentence_transformers
export WIKIFY_SIMPLE_DISPATCH_DIR=data/dispatch        # default
```

### 2. Model + budget

| knob | recommended for mvp20 | why |
|---|---|---|
| `--model` | `haiku` | extractor is the hot loop; haiku at ~$0.25/MTok keeps the run cheap |
| `--strategy` | `mixed` | exploit/explore split, the default for first runs |
| `--budget` | `300000` haiku-eq | enough for ~700 extract calls + ~150 write calls + headroom |

Token estimate (mvp20, 689 chunks, ≤208 pages), with token-based cost
accounting + per-tier overhead (`infra/cost_meter.py`):

| call | tier | tokens in / out | per-call heq |
|---|---|---|---|
| extract (no images) | S | 250 / 120 | ~420 |
| extract (+10 images) | S | 650 / 120 | ~820 |
| write (no figures) | M | 300 / 120 | ~5_600 |
| write (+14 figures) | M | 1000 / 120 | ~14_000 |

`per-call heq = input_tokens * input_per_m + output_tokens * output_per_m + fixed_overhead`
with S = (1.0, 1.0, 50), M = (12.0, 15.0, 200), L = (60.0, 75.0, 500).

Writer was re-tiered from L to M in the mixed/exploit strategies:
writer prompts are skeleton + evidence + figures + neighbors, which
easily fit in the M-tier context. The previous L tiering was ~5x more
expensive with no meaningful quality headroom.

For mvp20: ~700 extracts × ~600 heq + ~150 writes × ~10_000 heq ≈
420k + 1.5M heq. Budget for a full pass is ~2M heq at strategy M
(mostly write-bound). `--budget 300000` now buys ~25-30 full writes on
top of the extract loop instead of < 1 — useful for a smoke, but the
real run still wants ~2M heq for full coverage.

### 3. Skills available in the outer session

The outer Claude Code session needs all three skill files reachable:

- `/wikify_simple/extract` — reads `data/dispatch/extract/*.request.json`,
  emits an `ExtractResponse`-shaped JSON to the matching `.response.json`.

  **Extract schema (slice 6+)**

  Every concept in the response MUST satisfy:

  - `kind` is the page-type discriminator and is exactly `"concept"` or
    `"person"`. It drives directory routing
    (`concepts/<id>.md` vs `people/<id>.md`). Do not invent other values.
  - `category` is a facet tag, NOT a type. Allowed values are
    `"phenomenon" | "method" | "material" | "device" | "theory" |
    "metric" | "organization" | "other"` or `null` / omitted. It is
    always `null` for `kind="person"` (people do not get a category).
    For `kind="concept"` it is optional — `null` means "not classified".
  - `title` is 2..120 characters, not a stopword
    (`the a an of and or to is this that these those`), and has no
    leading/trailing punctuation.
  - `aliases` is a list of distinct strings, deduped case-insensitively,
    with any entry equal to the title (case-insensitively) removed.
    Capped at 8 entries by the binding.
  - `quote` is 5..400 characters AFTER stripping and MUST be a verbatim
    substring of the request's `chunk_text`. The binding enforces this
    after schema validation; a paraphrased quote triggers
    `QuoteNotInChunkError` and writes a `<rid>.error.json` artifact.
  - Pick a short distinctive 5..15 word phrase for the quote; verify
    by literal substring search against `chunk_text` BEFORE writing the
    response.

  **Rejection artifacts.** On any validation failure (schema or the
  binding's quote-substring check) the binding writes
  `<rid>.error.json` next to the request and KEEPS the request file for
  operator inspection. Only the response file is cleaned up. There is
  no silent retry: re-validating the same dict against the same schema
  cannot succeed.

  **Example response JSON** (method-typed concept + person):

  ```json
  {
    "chunk_id": "doc-a#chunk-003",
    "concepts": [
      {
        "title": "Atomic Layer Deposition",
        "aliases": ["ALD"],
        "kind": "concept",
        "category": "method",
        "quote": "atomic layer deposition is a self-limiting",
        "evidence_figures": []
      },
      {
        "title": "Chua",
        "aliases": ["Leon Chua"],
        "kind": "person",
        "category": null,
        "quote": "Chua introduced the memristor concept in",
        "evidence_figures": []
      }
    ],
    "tokens_in": 240,
    "tokens_out": 110
  }
  ```
- `/wikify_simple/write` — same shape for `WriteResponse`. Must include
  `used_markers: list[str]` (the list of `eN` identifiers referenced in
  the prose) and a `body_markdown` that passes the structural validator
  (>=2 non-blank prose lines before `## Evidence`, >=1 `[^eN]` marker in
  the prose, >=1 `[^eN]:` footnote definition in the evidence block).
  Example response body:

  ```json
  {
    "page_id": "concept-atomic-layer-deposition",
    "body_markdown": "ALD is a self-limiting surface reaction[^e1].\n\nIt produces conformal films one half-cycle at a time[^e2].\n\n## Evidence\n\n[^e1]: self-limiting half-reaction (doc-a)\n[^e2]: conformal over trenches (doc-b)\n",
    "used_markers": ["e1", "e2"],
    "tokens_in": 300,
    "tokens_out": 120
  }
  ```
- `/wikify_simple/query` — same shape for `QueryResponse`.

The skill files live at `.claude/skills/wikify_simple/`. Confirm they
match the schemas in `src/wikify_simple/agents/schema.py` (Pydantic v2,
`extra="forbid"` — any extra field rejects the response).

## Step-by-step

### 1. Ingest mvp20 fresh

```bash
rm -rf data/wikify_simple/corpora/mvp20_real
uv run python -m wikify_simple.cli ingest \
  --source data/papers/mvp20 \
  --corpus data/wikify_simple/corpora/mvp20_real
```

Verify before continuing:

```bash
uv run python -c "
import json
from pathlib import Path
from wikify_simple.paths import CorpusPaths
from wikify_simple.store.images_index import ImageIndex
c = CorpusPaths(Path('data/wikify_simple/corpora/mvp20_real'))
docs = list(c.docs_dir.glob('*.json'))
meta = json.loads((c.root/'vectors.meta.json').read_text())
idx = ImageIndex.load(c)
print('docs:', len(docs))
print('vectors.meta:', meta)
print('image-index docs:', len(idx.by_doc), 'images:', sum(len(v) for v in idx.by_doc.values()))
"
```

Expected: 20 docs, `{backend: sentence_transformers, dim: 384, model: all-MiniLM-L6-v2}`,
~19/20 docs with images, ~150-200 images total.

### 2. Distill with the real binding

```bash
uv run python -m wikify_simple.cli distill \
  --corpus data/wikify_simple/corpora/mvp20_real \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --strategy mixed \
  --binding claude_code \
  --budget 300000 \
  --model haiku \
  --seed 0
```

What happens during the run:

1. The harness loads the corpus, vectors, graph, image index, and the
   chunk sampler.
2. For each sampled chunk it writes
   `data/dispatch/extract/<rid>.request.json` and blocks polling for
   `data/dispatch/extract/<rid>.response.json` (timeout 600 s,
   poll 0.25 s).
3. The outer Claude Code session sees the request file (you can wire
   a hook that auto-runs `/wikify_simple/extract` on file create, or
   you drive it manually) and writes the response.
4. Python validates the response against `ExtractResponse`, charges
   the cost meter, optionally writes the result to `ExtractCache`, and
   moves on.
5. After the extract budget is spent, the harness canonicalises
   candidates into pages, then runs the writer loop with the same
   request/response dance against `data/dispatch/write/`.
6. Crosslink + page write + index rebuild + run snapshot.

### 3. Inspect the cost meter mid-run

The harness writes `_run.json` snapshots periodically. Watch with:

```bash
watch -n 5 'cat data/wikify_simple/wikis/mvp20_real_M/M_*/​_run.json | python -m json.tool | head -40'
```

Useful fields: `budget_used_haiku_eq`, `by_role.{extractor,writer}.calls`,
`by_role.*.cache_hit_rate`, `by_role.*.headroom_min`.

### 4. Abort cleanly

`Ctrl-C` in the harness terminal. The dispatcher request files in
`data/dispatch/{extract,write}/` may need manual cleanup:

```bash
rm -f data/dispatch/extract/*.request.json data/dispatch/extract/*.response.json
rm -f data/dispatch/write/*.request.json data/dispatch/write/*.response.json
```

The cache (`data/cache/extract/`) survives. Re-running picks up where
the budget left off. Pass `--feed` to merge against an existing bundle
instead of overwriting it.

Cache entries are now namespaced by binding under
`data/cache/extract/<binding_name>/<model_id>/<prompt_hash>/<chunk_hash>.json`
so fake-binding artifacts can never be served to a claude_code lookup.
Any cache files from before this change live at
`data/cache/extract/<model_id>/...` and are orphaned — they are not
deleted automatically; delete them manually if you want to reclaim the
disk space.

## Post-run

### 1. Eval

```bash
uv run python -m wikify_simple.cli eval \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --corpus data/wikify_simple/corpora/mvp20_real
```

Writes `_metrics.md` + `_metrics.json` next to the bundle (or where
`--report` says).

### 2. What good looks like for mvp20

| metric | fake binding (c4e1244) | hoped-for real binding |
|---|---|---|
| M1 coverage_residual | 0.5591 | < 0.30 |
| M3 g_evidence modularity | 0.0 | > 0.30 |
| M3 g_evidence n_edges | 0 | > 50 |
| M3 g_links modularity | NaN (skipped) | > 0.20 |
| M5 hit_rate | 0.0 | > 0.40 |
| M6 grounding pass | False | True |
| Pages with non-empty evidence | 1 / 208 | > 0.7 of total |
| Page bodies referencing figures | 0 | > 30 % |

The "what good looks like" column is a guess; the first real run will
calibrate it. If anything is far off, that's a finding.

### 3. Query it

```bash
uv run python -m wikify_simple.cli query \
  --bundle data/wikify_simple/wikis/mvp20_real_M \
  --binding claude_code \
  "what is a memristor?"
```

Result file lands at
`data/queries/<bundle_name>/<utc_iso_compact>.md`. The bundle is never
mutated by a query call.

## Troubleshooting

### Dispatcher hang

`TimeoutError: no response at <path>` after 600 s. The outer session
isn't responding. Check:

1. Is the skill enabled in the outer Claude Code session?
2. Does the request file exist? (`ls data/dispatch/extract/`)
3. Did the skill emit a `.response.json` next to the `.request.json`?
4. Are the request paths and response paths in the same directory? The
   binding strips the request file's `.request.` segment to derive the
   response path; the skill must mirror that.

### Schema validation failure

`pydantic_core._pydantic_core.ValidationError`. The response file has a
shape the model rejects. The schemas are strict (`extra="forbid"`), so
*any* unexpected key fails. Read `src/wikify_simple/agents/schema.py`
for the canonical shape; the skill prompt files must produce JSON that
matches exactly.

### Budget exhaustion mid-write

The extractor over-consumed and the writer ran out of budget after a
few pages. Re-run with a higher `--budget` or shift the
extract/write split in the strategy schedule
(`distill/strategies/mixed.py`).

### Cache miss explosion

Every chunk is a cache miss. Likely the prompt template or model id
changed since the last run, invalidating every cache key. Verify
`prompt_hash` is stable across runs.

### MAX_PATH on Windows

Already fixed in `c9f2a0c`-area: `ExtractCacheKey.relpath` hashes the
chunk_id and the ingest image folder uses a word-bounded ≤80-char
slug. If you see a fresh path-too-long error, check what new field is
being written to disk.

## Open questions this run answers

1. **Does M1 actually drop?** Fake binding sat at 0.5591 because pages
   have no real prose to embed. A real writer should bring page
   embeddings close to the chunk embeddings they were grounded in.
2. **Does M3 g_evidence have non-zero modularity?** Requires that >1
   page has evidence and that pages cluster by source paper / topic.
   The fake binding produces 1 page with evidence; the real binding
   should produce ~150.
3. **Does M6 grounding pass?** The fake writer never appends an
   `## Evidence` block, so footnote markers don't resolve. Real
   bindings should write `[^e1]: <chunk_id> (<doc_id>) > "..."`
   blocks the bundle parser can read.
4. **Do figure references via ImageIndex actually land in page bodies?**
   `WriteRequest.figures` is populated by `distill/pipeline`. The
   writer prompt mentions them; whether the model actually embeds
   `![Figure 1](path)` references is the open question. Count occurrences
   in `data/wikify_simple/wikis/mvp20_real_M/M_*/concepts/*.md` after
   the run.
5. **Are the junk concept titles gone?** FakeExtractor spat out
   `concept-thoroughly`, `concept-categorizes`, etc. A real
   small-model extractor should produce ~30-60 substantive concepts
   instead of 156 noise.

Record answers in `slice6_findings.md` under a new
`## Real-binding run (commit <hash>)` heading.
