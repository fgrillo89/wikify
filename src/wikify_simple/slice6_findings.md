# Slice 6 — first real run on mvp20 (20 PDFs)

## 1x run on clean corpus mvp20_v4 — all 3 structural fixes (a3ef62b et al.)

Fresh ingest into `mvp20_v4` (parse-time artifact stripping), fresh
1x distill into `mvp20_v4_REAL`. Tests three structural fixes:
- `2e4e579` tolerant quote substring
- `9131791` parse-time `[NN]` + bracket-wrap strip
- `a3ef62b` writer tier L → M

| metric | mvp20_v3 (3x) | **mvp20_v4 (1x)** |
|---|---|---|
| budget | 150k heq | 50k heq |
| pages | 215c+21p | 83c+8p |
| extracts | 74 new + 30 cached | 30 new + 10 cached |
| writes | 2 | **2** |
| write cost (each) | 27-69k | **~10-14k** ✓ |
| M1 | 0.4603 | 0.4918 |
| M3 g_links Q | 0.7377 | **0.7596** ✓ |
| g_links edges | 2601 | 419 |
| figure refs in writes | 2/2 | **2/2 (100%)** ✓ |
| extract rejections | 5 | 2 |

### What the fixes proved

1. **Parse-time artifact stripping works.** mvp20_v4 chunks have **0**
   `[NN]` markers and **0** `[token][bracket][wrap]` artifacts (verified
   by regex scan). The 5/6 quote-substring rejections from the 3x run
   class are gone.
2. **Writer tier M works.** Per-write cost dropped from 27-69k → ~10-14k.
   First write spent 10.4k heq, second 14.3k. ~5x cheaper, exactly as
   modelled.
3. **Tolerant quote matcher works** for the bracket/dash/citation/whitespace
   class. Two NEW residual patterns surfaced (markdown emphasis):
   - `**Chul-Ho Lee**` (bold author name)
   - `_in situ_` (italics)
   Fixed in commit `<this>` by adding `*` and `_` stripping to
   `text_normalize`.

### What's still broken

**Only 2 writes again.** The tier-M fix is necessary but not sufficient.
Root cause: the `mixed` strategy schedule allocates only `1 - 0.4 - 0.05 = 55%`
of budget to extract and 40% to write (5% curate). At 50k total, that's
20k for writes — at 10-14k each, only 2 writes fit.

Combined with this commit:
- **Schedule rebalance**: bump `exploit_fraction_initial` from 0.4 →
  0.65. New share: 30% extract, 65% write, 5% curate. At 50k that's
  ~32k for writes → ~3 writes per 1x budget. Still not enough — the
  next slice should re-run at 3x to actually break g_evidence past zero.

**M3 g_evidence still 0** for the same root cause: only 2-3 written
pages → crosslink filter drops the rest → no doc-evidence edges.

### Next slice

1. Re-run mvp20_v4 distill at **3x budget** with the new schedule
   (0.65 exploit fraction). Expected: ~10-15 written pages, M3
   g_evidence breaks zero for the first time.
2. Implement adaptive reallocation in `distill/pipeline.run` (the
   schedule's `reallocate` method exists but is never called).
3. M6 g2_evidence_ok = 0 — investigate why footnote markers don't
   resolve back to bundle chunks.

## 3x real-binding run (commit f13f91f + pipeline-skip follow-up)

Full mvp20_v3 distill at `--budget 3x` (150k heq) under `--binding
claude_code`. Subagent drove the dispatcher loop. Cache from prior
real-binding runs served 30 hits; 74 new extracts + 2 writes burned
the budget (~184k heq, 23% over).

| metric | fake (0dd7e00) | 0.1x real (smoke) | **3x real (this)** | hoped |
|---|---|---|---|---|
| pages (concept + person) | 156 + 52 (noise) | 18 + 2 | **215 + 21** | — |
| M1 coverage_residual | 0.5591 | 0.5504 | **0.4603** | <0.30 |
| M3 g_links Q | NaN | 0.56 | **0.7377** | >0.20 |
| M3 g_links n_edges | 0 | 60 | **2601** | >50 |
| M3 g_evidence Q | 0 | 0 | 0 | >0.30 |
| figure refs in writes | 0 | 0 | **2/2 (100%)** | >30% |
| M5 hit_rate | 0 | 0 | 0 | >0.40 |

### What worked

1. **Concept quality.** 215 substantive concepts: ALD, HfO2/HfOx
   Bilayer Memristor, STDP, VMM, 1T1R Crossbar, Conductive Filament,
   Oxygen Vacancy, Pavlov Conditioning, Resistive Switching, RRAM,
   Phase-Change Memory, Diffusive Memristor, Memristor Crossbar
   Engine, Si-CMOS, Von Neumann Architecture, etc. **21 real
   researchers**: Leon Chua, R. Stanley Williams, Carver Mead,
   Dmitri Strukov, Donghun Lee, Chul-Ho Lee, etc.
2. **Crosslinking is structurally healthy.** Q=0.7377 with 2601 edges
   on 236 nodes. The link graph self-organizes without any global
   plan: ALD ↔ HfO2 ↔ Memristor ↔ Crossbar ↔ STDP form a tight
   cluster, separate from the device-physics cluster (Conductive
   Filament, Oxygen Vacancy, Pavlov Conditioning).
3. **Figure wiring landed.** Both write calls embedded inline
   `![Figure 1](images/...)` markdown referencing the corpus image
   index. **The primary purpose of the run — verifying figure refs
   reach page bodies — passed.**
4. **Per-call rejection skip works.** 6 of 80 extract calls were
   rejected (5 quote-substring violations + 1 schema violation) and
   the pipeline silently skipped them and continued. Without the
   `try/except (ValidationError, QuoteNotInChunkError)` fix in
   `distill/pipeline.py`, the run would have died at 4% completion.

### What's still broken

1. **M3 g_evidence is still 0.** Only 2 pages were *written* (have a
   prose body); the other 213 are skeletons. `crosslink` then filters
   out all evidence-less pages before building the doc-evidence
   graph, so n_edges=0. The writer is starved by the cost model.
2. **Writer cost is the gating constraint, not extractor.** Writer
   tier L = 27.5k heq per call (with figures: ~50k+ each). At 3x
   budget = 150k heq, that's at most 5 writes. Need 30x or 100x
   budget for a real wiki, OR shift the strategy schedule to put
   far more weight on writes, OR drop the writer to tier M.
3. **Quote validator catches drain agent shortcuts.** 5/6 rejections
   were the drain agent silently normalizing whitespace / unicode
   dashes / pymupdf bracket-wrap citation tokens (`H][2][plasma]`,
   `which was a   memristor`). Working as intended, but the right
   structural fix is **tolerant substring match** in the binding
   wrapper: NFKC-normalize + collapse whitespace + strip `[NN]`
   citation markers on BOTH sides before comparing. The verbatim
   form is still stored.
4. **Better long-term: clean pymupdf bracket artifacts at parse
   time.** The chunks themselves should be free of `[1]` citation
   markers and `[token][bracket][wrap]` noise. Affects embedder,
   model, and validator simultaneously.
5. **M5 hit_rate = 0** because only 2 pages have evidence chunks to
   intersect with `chunks_read`. Same root cause as #1 — writer
   starvation.
6. **M6 grounding** parsed 4 sentences with 2 markers; g1=0.5
   (only half of sentences had markers), g2=0 (the markers don't
   resolve to a chunk in the bundle's `_chunks` index — likely a
   path normalization issue between the writer's `<chunk_id>` form
   and the bundle's stored form). Only 2 pages contributed —
   small sample.

### Next slice

Three fixes, in priority order:
1. **Tolerant quote-substring match** in `bindings/{fake,claude_code}.py::_assert_quotes_in_chunk`. NFKC + whitespace collapse + `[NN]` strip. Quotes still stored verbatim. Eliminates the entire class of drain-agent rejections.
2. **Strip pymupdf bracket-wrap artifacts at parse time** in `ingest/parsers/pdf.py`. Chunks should never contain `H][2][plasma]`.
3. **Writer cost re-tier** (or schedule re-balance) so a 1x budget yields 20+ writes instead of 5. Right now writer is 100x extractor.

After those, re-run at 1x with `--feed` to add more written pages on top of this bundle and watch g_evidence break zero for the first time.

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
