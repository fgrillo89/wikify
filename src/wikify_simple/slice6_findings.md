# Slice 6 — first real run on mvp20 (20 PDFs)

First end-to-end run of `wikify_simple` against a real corpus:
`data/papers/mvp20/` — 20 memristor/ALD/neuromorphic PDFs.

## Setup

- Corpus: `data/wikify_simple/corpora/mvp20/`
- Bundle: `data/wikify_simple/wikis/mvp20_M/M_1x_seed0_20260408T075209/`
- Embedder: `sentence_transformers` (all-MiniLM-L6-v2, 384-d) at ingest time
- Distill: `--strategy mixed --binding fake --budget 50000 haiku_eq`

## What worked

- **Ingest:** all 20 PDFs parsed. 689 chunks produced. 312 images
  extracted across 18 of the 20 papers with JSON sidecars carrying
  caption / bbox / label / page / content_hash. Vectors.npz shape
  `(689, 384)`. Corpus graph and topics.json built.
- **Image persistence:** `corpus/images/{doc_id}/fig_NNN.{ext}` + `.json`
  sidecar round-trip holds; `read_doc_images(doc)` returns the list.
- **Sentence-transformers embedder:** loaded, ran, produced 384-d vectors
  (confirms `WIKIFY_SIMPLE_EMBEDDER=sentence_transformers` path).
- **Distill pipeline:** ran to completion, wrote 156 concept pages + 52
  person pages, `_index.json`, `_index.md`, `_run.json`, `_calls.jsonl`.
- **Query mode:** `wikify-simple query --binding fake "what is a memristor?"`
  resolved "memristor" via the alias map (first citation:
  `concept-memristor`), wrote `data/queries/.../20260408T093126.md`, did
  not mutate the bundle.
- **Tests:** 30/30 pass after the run. `check_no_vendor_imports.py` OK.

## What broke

### 1. Windows MAX_PATH blew up the extract cache — fixed inline

`ExtractCache` path was `{model}/{prompt_hash}/{chunk_id}.json`. Real
chunk ids include the full doc filename (e.g.
`[2022 Ismail] Demonstration of synaptic and resistive switching characteristics in W TiO2 HfO2 TaN memristor crossbar array for bioinspired neuromorphic computing__sec_42_c07`)
which pushed the total path past Windows' 260-char MAX_PATH and the
cache write exploded. Fixed in `infra/cache.py::ExtractCacheKey.relpath`
by hashing `chunk_id` to a 24-char sha256 prefix. Cache key (model +
prompt_hash + chunk_id) is unchanged; only the on-disk filename is
shorter. No migration needed because no cache artifacts shipped.

### 2. Embedder-dimension mismatch at eval time — not fixed

`M1 coverage_residual` failed:
`size 128 is different from 384`. The corpus was ingested with
sentence-transformers (384-d) but `coverage_residual`'s default embed
callable falls back to the `hash` backend (128-d) because the
`WIKIFY_SIMPLE_EMBEDDER` env var wasn't set in the eval-time process.
`infra/embedding.py` reads the env var at import time; the ingest run
was already over by the time eval ran.

**Fix needed (next slice):** persist the embedder backend that was used
to build `vectors.npz` into a sibling `vectors.meta.json` alongside
`corpus/vectors.npz`, and have `infra.embedding.embed_texts` read that
file when a CorpusPaths handle is in scope — or simpler: make
`coverage_residual` take the embedder as an explicit required argument
the caller has to construct from the corpus metadata. The current
"embed is a callable, registries decide which one" shape is too loose.

### 3. `G_evidence` is empty — budget exhausted on extract

`_run.json`: extractor burned `25760 haiku_eq / 92 calls`, writer wrote
**1 page** before the budget ran out. 207/208 pages are skeleton
concepts with zero evidence, so the doc-level cosine adjacency has
zero edges and `M3_g_evidence = {modularity: 0.0, spectral_gap: 0.0,
n_edges: 0}`. Expected artifact of the fake binding + a budget gate
that's over-indexed on extract. Not a bug in the metric.

**Fix needed:** the `mixed` strategy's default schedule puts ~50% of the
budget on extract and ~50% on write; with FakeExtractor's 280-token
cost-per-call, 92 extract calls consumed the extract half of the
budget and writer got one shot. With a real binding each write call
costs more and the balance flips. Leave the schedule alone but
re-run under the claude binding (where costs are real) before drawing
any conclusions.

### 4. FakeExtractor produces garbage concept titles

The wiki is polluted with `concept-addition`, `concept-another`,
`concept-applied`, `concept-categorizes`, `concept-thoroughly`, etc.
Expected — FakeExtractor grabs random noun phrases from the chunk.
Not fixable without real extraction; flagged so we don't look at the
current wiki and conclude the pipeline is broken.

### 5. `M3_g_links`, `M5_hit_rate`, `M6_grounding` not captured

`scripts/slice6_metrics.py` printed M1 and M3_g_evidence then the
output buffer stalled (Windows/uv subprocess pipe issue — the file
never flushed the remaining lines before the shell wrapper gave up).
The rest of the script runs fine locally. Not a pipeline bug; a
harness-ergonomics issue with how the run was invoked here. A
follow-up should add `wikify-simple eval --bundle <dir>` as a proper
CLI verb so metrics are a first-class command instead of a script.

## Recommended next slice

1. Wire `vectors.meta.json` + make `coverage_residual`'s embedder
   explicit (fixes #2).
2. Add `wikify-simple eval --bundle <dir>` verb (fixes #5, makes
   metrics reproducible).
3. Re-run slice 6 with the `claude_code` binding and a 500k haiku_eq
   budget against the same mvp20 corpus. That is the first run where
   the M1/M3/M5/M6 numbers will actually mean anything.
4. Only then start item 6 (graphify patterns).

## Artifacts (not committed — `data/` is gitignored)

- Corpus: `data/wikify_simple/corpora/mvp20/`
  (20 docs, 689 chunks, 312 images, 384-d vectors)
- Bundle: `data/wikify_simple/wikis/mvp20_M/M_1x_seed0_20260408T075209/`
  (156 concept skeletons, 52 person skeletons, 1 written page)
- Query result: `data/queries/M_1x_seed0_20260408T075209/20260408T093126.md`
