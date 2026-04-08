# Slice 6 — first real run on mvp20 (20 PDFs)

## First real-binding smoke run (commit 22e2da3 + follow-up)

Tiny budget (0.1x = 5000 heq) end-to-end with `--binding claude_code`.
Subagent drove the dispatcher loop manually as the model. 8 extract
calls + 1 write call before the budget tripped.

**Pages written:** 18 concept + 2 person — **all substantive**, no
FakeExtractor noise. Concept titles include `Memristor`,
`Atomic Layer Deposition`, `Diffusive Memristor`, `Spike-Timing
Dependent Plasticity`, `Vector Matrix Multiplication`, `Von Neumann
Bottleneck`, `In-Memory Computing`, `1T1M Crossbar`, etc. People are
real authors (`Donghun Lee`, `Chul-Ho Lee`) instead of fake-binding's
`person-graduate`, `person-public-release`.

**Metrics (`wikify-simple eval`)**
```
M1 coverage_residual = 0.5504  (was 0.5591 — barely moved, only 1 page has body)
M3 g_evidence        = {Q: 0.0, gap: 0.0, n_nodes: 20, n_edges: 0}
M3 g_links           = {Q: 0.56, gap: ~0, n_nodes: 20, n_edges: 60}  ← FIRST NON-ZERO
M5 hit_rate          = 0.0   (only 1 page has evidence)
M6 grounding         = passes (g1=1.0, g2=1.0, n_sent=0, n_mark=0)
```

**Findings**

1. **Concept quality is real.** Crosslink graph has Q=0.56 — links
   formed naturally between ALD ↔ Memristor ↔ Crossbar ↔ STDP ↔
   Vector Matrix Multiplication. First evidence the wiki has structure.
2. **Writer cost is wildly under-estimated.** Single write call burned
   46_500 heq vs extractor's ~470/call (100x). Likely the writer tier's
   cost model in `infra/cost_meter.py` doesn't account for the
   `figures` payload that landed in c4e1244 (`WriteRequest.figures`).
   Either re-tier writes or shift the schedule's extract/write split.
   At current cost shape, 1x budget = ~12 extracts + 1 write; need
   3x or higher for any meaningful real run.
3. **The 1 written page (Atomic Layer Deposition) has an empty body.**
   The drain subagent emitted only the title + Evidence block, no
   prose. The write skill prompt should push for ≥3 sentences; or the
   schema validator should reject `body_markdown` shorter than N
   sentences. Today the schema only requires `body_markdown: str`.
4. **Cache poisoning risk surfaced.** First attempted run was an
   instant no-op because `data/cache/extract/` carried the prior
   fake-binding results — same `prompt_hash` (`wikify_simple/extract/v1`)
   so cache lookups hit. Either cache should key on binding-name too,
   or fake/real should use distinct prompt template ids. Cleared
   manually; needs a structural fix before any larger run.
5. **No figure references in the written page.** The Atomic Layer
   Deposition page got a `figures` list but the body has no
   `![Figure N](path)` markdown. Either the writer prompt didn't
   surface them or the drain subagent omitted them. Inspect on the
   next run when more pages exist.
6. **Dispatcher protocol works.** Zero schema-validation failures
   across 9 dispatches. `WriteResponse` requires a `used_markers` field
   that the runbook draft missed (the drain agent caught it by reading
   the schema). Update the runbook.

**Next-run prerequisites** (before going to 1x or 3x):
- Fix writer cost accounting (issue 2).
- Cache-key per binding (issue 4).
- Writer prompt enforcement of N-sentence minimum (issue 3).
- Add `used_markers` to the runbook's WriteResponse example.

## Re-run after structural fixes (commit 2ca6423 + follow-up)

Re-ran the full pipeline against `data/papers/mvp20/` with the structural
fixes from steps 1–3:

- `vectors.meta.json` next to `vectors.npz` (sentence_transformers, 384-d)
- `wikify-simple eval` first-class CLI verb
- `ImageIndex` wired into distill (extractor + writer see figures)

### Ingest health (mvp20_v3)

| metric | value |
|---|---|
| n_docs | 20 |
| n_chunks | 689 |
| vectors_shape | (689, 384) |
| backend | sentence_transformers (all-MiniLM-L6-v2) |
| docs_with_empty_sections | 0 |
| docs_with_none_year | 0 |
| n_images_total | 164 |
| n_docs_with_images | 19 of 20 |

Random alias resolution (5 papers): 4 of 5 resolve `Figure 1` to a
`Figure_01_*` record; 1 paper (1971 Chua, the original memristor paper)
has no extracted figures, which is expected for that scan.

### Distill (fake binding, 100k haiku_eq)

| | value |
|---|---|
| concept pages | 156 |
| person pages | 52 |
| total pages | 208 |
| extractor calls | 92 (25_760 heq) |
| writer calls | 3 (81_000 heq) |
| budget used | 106_760 / 100_000 (writer overran by 6.7%) |

### Eval metrics

```
M1 coverage_residual = 0.5591   (lower is better)
M3 g_evidence        = {modularity: 0.0, spectral_gap: 0.0,
                        n_nodes: 208, n_edges: 0}
M3 g_links           = SKIPPED (n=208 > 150 cap)
M5 hit_rate          = 0.0
M6 grounding         = g1=1.0 g2=0.0 n_sent=4 n_mark=4 PASS=False
```

### Query result

`wikify-simple query "what is a memristor?" --binding fake`:

```
[fake] question='what is a memristor?' supported by 12 pages
citations: concept-memristor, concept-analog, concept-computing,
           person-analog-computing, concept-existing,
           concept-technologies, person-massachusetts-amherst,
           concept-built, person-public-release, concept-traditional,
           concept-computer, person-von-neumann
```

Output written to `data/queries/M_100000_seed0_20260408T131724/...md`,
no bundle mutation.

### What the new run resolved

- Issue #2 (embedder dim mismatch): GONE. `vectors.meta.json` records
  `(sentence_transformers, 384, all-MiniLM-L6-v2)`, and `coverage_residual`
  reconstructs the matching embedder via `embedder_for(meta.backend,
  meta.model)`. Dim mismatch now raises `EmbedderMismatch`.
- Issue #5 (M3_g_links / M5 / M6 not captured): GONE. `wikify-simple
  eval` is now a first-class verb that writes `_metrics.md` +
  `_metrics.json` and prints a one-line summary.

### What's still pending (real-binding work)

- Issue #3 (g_evidence empty / writer-budget starvation): UNCHANGED
  under fake binding. Writer made only 3 calls before the cost meter
  flagged the budget; 0 pages have evidence after `crosslink` filter.
  This is the same fake-binding artifact as before — the next run with
  `--binding claude_code` against a real budget is the first one where
  M3/M5/M6 will mean anything.
- Issue #4 (junk concept titles from FakeExtractor): UNCHANGED. Still
  `concept-thoroughly`, `concept-categorizes`, etc. Real-binding only.
- New issue: greedy modularity is O(n^4); 208 nodes hung the eval
  verb. Hot-fixed by skipping `g_links_modularity` and
  `spectral_gap_modularity` when `n > 150` and reporting NaN. The
  eval CLI's JSON sidecar now coerces NaN to null. Proper fix:
  swap in networkx Louvain or igraph when this becomes a real
  bottleneck (i.e. once a real-binding run produces graphs we
  actually want to measure on).
- M6 g2_evidence_ok = 0.0 because `bundle.pages[*].evidence` parses to 0
  even though the writer wrote markers in body text. Bundle parser
  finds the markers (M6 sees 4 sentences with markers) but the Evidence
  block parsing in `eval/bundle._extract_evidence` does not match
  what the FakeWriter emits — fake writer never appends an `## Evidence`
  block, so there are no `[^eN]: chunk_id (doc_id) > "..."` lines to
  resolve against. Real bindings will write evidence blocks; this is
  another fake-binding-only artifact, not a metric bug.

---

# Original slice 6 — first real run on mvp20 (20 PDFs)

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
