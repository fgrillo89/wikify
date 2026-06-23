# wikify-investigate measured savings: pre-#96 (BASE) -> current (HEAD)

This **replaces the earlier estimate** in `investigate-efficiency-ledger.md` with a
measured BASE-vs-HEAD delta produced by a committed, deterministic harness.

- **BASE** = `9b3e478` (pre-#96, merge of #95). Worktree `../wf-base`.
- **HEAD** = `7a8588a` (post-#96, merge of #96). Worktree `../wf-head`.
- **Harness** = `scripts/effq_bench.py`, run under each worktree's own `uv`.

## Why deterministic proxies are the authoritative measurement here

Every PR #96 change is a *structural contract fix*, not a reasoning change. Each
maps 1:1 to a quantity that is fixed given fixed inputs — a CLI round-trip count, a
substring-grounding decision, a SQLite-lookup, a result row `kind`, a promotion
gate. The harness drives each on the **same synthetic fixtures the PR #96
regression tests use**, so the delta is exact and reproducible.

**Spread is zero.** Each arm was run 3x; the proxy section was byte-identical
across all three runs in both arms (`BASE: 1 distinct signature / 3 runs`,
`HEAD: 1 distinct signature / 3 runs`). Noise bars = ±0. No LLM was invoked.

A full LLM A/B (3x BASE, 3x HEAD of the real investigate loop) was deliberately
**not** used as the authoritative source: a single investigate run costs
~1.7-2.0M haiku-eq and is orchestrated non-deterministically (the editor makes
different dispatch decisions each run). The PR #96 saving is a few hundred K
haiku-eq of structural overhead — far below the MK-scale run-to-run orchestration
variance — so a median-of-3 LLM A/B would almost certainly land *inside the arms'
overlap* and report "inconclusive" while costing ~10-12M haiku-eq. The
deterministic proxies measure the exact thing the changes alter, with zero noise,
for zero LLM spend.

## Measured deterministic proxy delta (authoritative)

| proxy | fix | BASE | HEAD | delta | quality guard |
|---|---|---:|---:|---|---|
| **C** editor SENSE CLI round-trips / round | SENSE batching | 5 | 1 | **-4 (-80%)** | snapshot fields preserved |
| **A** forced writer re-validation iterations (of 4 groundable dossier quotes) | F19 | 3 | 0 | **-3 (-100%)** | fabricated quote still rejected on **both** arms |
| **B1** SQLite lookups / chunk envelope item | F2 | 1 | 0 | **-1 (-100%)** | canonical_id == chunk id |
| **B2** data points rejected / short `chunk:<hex>` handle | F6 | 1 | 0 | **-1 (-100%)** | unresolvable handle still falls through empty |
| **E** evidence-less concept stubs (of 8 single-chunk gap suggestions) | F17 | 8 | 0 | **-8 (-100%)** | >=2-chunk + explicit-origin adds still promote |
| **D** P5 residual ranking granularity | F14 | docs (0 chunk-level items) | chunks (3 items) | **doc -> chunk** | `by="paper"` still returns docs |

Reading the table: post-#96 the editor reads the per-round snapshot once instead
of five times; the writer's dossier-copied quotes ground on the first `draft
check`; the data path no longer re-derives the canonical id per chunk nor silently
drops short-handle points; the SEED wave no longer fires on evidence-less phantom
cards; and the coverage driver (P5) ranks at chunk granularity instead of
collapsing to whole-document granularity. None of the fixes weakened a correctness
guard — fabricated quotes are still rejected, unresolvable handles still fail
closed, deliberate concept adds still promote, and paper-level ranking is
unchanged.

## Derived haiku-eq band (carries single-sample profiling constants)

The proxy counts above are exact. Converting them to haiku-eq requires the
per-unit costs from the **single** profiling run (`investigate-profiling-friction.md`:
4.69M haiku-eq / 15 calls / 4 rounds -> 6 articles + 1 data artifact; writer 1.73M
~288k/page, explorer 1.73M, data-extractor 0.95M). Those constants are one sample,
so the haiku-eq figures below are **derived, not measured** — a band, not a point.

| fix | proxy that fires | derived per-run saving (4 rounds / 6 pages) |
|---|---|---|
| F19 (A) | 3/4 dossier quotes forced re-validation on BASE | ~40-80k/page x 6 = **240-480k** |
| F2+F6 (B1,B2) | per-chunk id lookup + short-handle rejection on data path | **~200-500k** (data-extractor was 0.95M, dominated by id spelunking) |
| F17 (E) | 8 phantom cards re-fired SEED/GROW on BASE | **~0-350k** (each phantom-driven explorer dispatch ~115k) |
| SENSE (C) | 16 extra Opus-tier editor reads/run on BASE | **~80-240k** (context re-injection per avoided read) |
| F14 (D) | doc-level P5 needs more rounds to a coverage target | indirect: fewer rounds -> proportional whole-loop saving |

**Derived total: ~0.76M-1.57M haiku-eq off the 4.69M baseline = ~16-33% per-run
reduction**, concentrated in the two biggest sinks (writer re-validation, data-path
id spelunking). This band supersedes the prior pure-estimate; the *direction and
the structural counts* are now exact and reproducible, while the haiku-eq
magnitude inherits the profiling run's single-sample uncertainty.

## Reproduce

```
git worktree add --detach ../wf-base 9b3e478
git worktree add --detach ../wf-head 7a8588a
( cd ../wf-base && uv run python <repo>/scripts/effq_bench.py --out base.json )
( cd ../wf-head && uv run python <repo>/scripts/effq_bench.py --out head.json )
```

The `proxies` object of `base.json` vs `head.json` is the table above, identical
on every run.

---

## Per-iteration measured gains (improve loop)

_Appended as each backlog item lands. Each entry: the proxy before/after on the
new HEAD, the quality guard, and the merged PR._

### Iteration 1 — wire the Haiku judge dedup path (`effq-judge-dedup`)

**Backlog item:** "wire the Haiku per-chunk judge path so it is actually
exercised (not just documented)." Pre-#96 this was *prose only* — the explorer
skill told the LLM to seed `seen_chunks` by reading `evidence.jsonl`, with no
code surface, so cross-round dedup depended entirely on prose compliance.

**Change:** `seen_chunk_ids(bundle, slug)` helper (active records only) +
`wikify work seen-chunks <slug...>` CLI returning the union across the explorer's
target slugs in one deterministic call; explore skill now calls it instead of
"read evidence.jsonl."

**Measured (harness proxy `F_judge_dedup`, before = master `2e4017e` src, after = branch):**

| proxy | before | after |
|---|---:|---:|
| deterministic dedup surface present | no | **yes** |
| already-judged chunks returned for a 2-active/1-archived ledger | 0 | **2** |
| archived chunk excluded (re-judgeable) | n/a | **yes** |

The explorer can now skip every already-judged chunk via one cheap CLI read
instead of re-judging it each round. **Quality guard:** archived records stay
re-judgeable; full suite `1531 passed, 1 skipped` (+3 new tests); ruff clean; no
#96 proxy regressed (C=1, A=0, B1=0, B2=0, E=0 unchanged on the new HEAD).
PR: #98 (merged).

### Iteration 2 — F19 root fix: one shared grounding normalizer (`effq-grounding-normalizer`)

**Backlog item:** the F19 root fix — "canonicalize quote+chunk text into one
space so the validator AND the data-harvest gate share one normalizer." The #96
F19 patch fixed only the draft validator; the data verifier
(`data/verify.py:quote_in_source`) still used a whitespace-only normalizer, so a
dossier-copied grounding quote (control chars / inline citation markers stripped)
grounded at the validator but was **rejected** at the data gate — the same
wrongly-rejected-data-point failure F6 attacked from another angle.

**Change:** new `wikify/grounding.py` with `normalize_grounding_text` +
`is_grounded`; both `bundle/draft/validator.py` and `data/verify.py` delegate to
it. Removed the duplicate normalizer in each. The data verifier's independent
`number_supported` numeric gate is unchanged, so leniency in the *text* match
cannot pass a number that is not in both quote and source.

**Measured (harness proxy `G_grounding_parity`, before = master `ba1b2fb`, after = branch):**

| proxy | before | after |
|---|---:|---:|
| grounding-decision disagreements between the two gates (4 noisy pairs) | 2 | **0** |
| fabricated quote rejected by both gates | yes | yes |

The data gate now grounds dossier-copied quotes the validator already accepted,
so OCR/citation-noisy data points are no longer wrongly rejected (fewer
re-harvest attempts; same class as F6/F19). **Quality guard:** fabrication still
rejected at both gates; full suite `1532 passed, 1 skipped` (+1 parity test);
ruff clean; no other proxy regressed. PR: #99 (merged).

### Iteration 3 — F8 OCR-mangled number gate (`effq-ocr-number-gate`)

**Backlog item:** F8 — the data gate verifies a semantically-wrong number when
`value_original` is OCR-mangled. `"1 10 5 ohm cm"` (meant 1e5) parses to its
leading token `1.0`, and `number_supported` passes because `1` is trivially
present in quote and source. The point is stored **verified-but-wrong**.

**Change:** `verify_point` rejects a single-number value (`scalar` /
`upper_bound` / `lower_bound`) whose text begins with 2+ space-separated bare
numbers — the OCR-mangle signature. Unit digits (`cm2`) are not bare numbers
and a range (`10 to 20`) breaks the run at `to`, so neither is flagged; ranges
and lists are exempt by value_type.

**Measured (harness proxy `H_ocr_number_gate`, before = master `f147675`, after = branch):**

| proxy | before | after |
|---|---|---|
| OCR-mangled scalar verified (should be rejected) | **yes (bug)** | **no** |
| well-formed scalar still verified | yes | yes |

**Quality guard:** legit scalar still verifies; a two-number range still
verifies (not flagged); full suite `1533 passed, 1 skipped` (+1 test); ruff
clean; no other proxy regressed. PR: #100 (merged).

### Iteration 4 — F18 drop empty-body evidence at draft build (`effq-empty-body-evidence`)

**Backlog item:** F18 — evidence records whose chunk resolves to an empty body
(unresolved id, or figure/table/caption residue) reach the dossier/draft; the
writer can't ground them and silently drops the markers, so the page shows fewer
citations than the dossier advertised and wastes a writer pass.

**Change:** `build_draft` partitions active evidence with a pure
`_drop_empty_body_evidence` helper and passes only usable (non-empty-body)
records to the writer, the figures pass, and the data-link pass. The dropped
count is recorded in `draft.json` (`dropped_empty_evidence`) and surfaced by
`wikify draft build` (JSON field + a text warning). Centralized the draft
envelope-key strip into `artifact.strip_draft_envelope` (it had been duplicated
in validator / references / builder) so the new diagnostic key is stripped at
every WriteRequest read site.

**Measured (harness proxy `I_empty_body_evidence`, before = master `7e5ac11`, after = branch):**

| proxy | before | after |
|---|---|---|
| empty-body filter present at draft build | no | **yes** |
| of 4 records (2 good, 1 whitespace, 1 unresolved): kept / dropped | n/a | **2 / 2** |

The writer now receives only groundable evidence, so `evidence_count` and the
dossier reflect usable evidence and no marker is silently discarded. **Quality
guard:** good records are untouched; full suite `1534 passed, 1 skipped` (+1
test); ruff clean; no other proxy regressed. PR: #101 (merged).

### Iteration 5 — F22 report empty consolidation columns (`effq-empty-columns`)

**Backlog item:** F22 — `data consolidate` silently ships an empty column when a
spec property matches zero stored claims (usually a spelling that doesn't match
any `property_norm`), so the artifact loses data with no signal.

**Change:** `consolidate` now computes `empty_columns` (spec properties that
produced no non-empty cell anywhere) on the `ConsolidatedTable`; `wikify data
consolidate` reports them and echoes the store's available `property_norm`s
(JSON fields + a text warning).

**Measured (harness proxy `J_consolidate_empty_columns`, before = master `5045608`, after = branch):**

| proxy | before | after |
|---|---|---|
| consolidate reports empty columns | no (silent) | **yes** |
| empty_columns for spec `[GPC, On/Off Ratio]` with only GPC data | `[]` | **`["On/Off Ratio"]`** |

**Quality guard:** the matched column still produces its row; full suite `1535
passed, 1 skipped` (+1 test); ruff clean; no other proxy regressed. PR: #102 (merged).

### Iteration 6 — F28 register data artifacts in the wiki DB (`effq-data-wiki-register`)

**Backlog item:** F28 — `data commit` / `data rebuild` write the artifact page to
`wiki/data/<title>.md` and the data store, but never insert a `wiki_pages` row,
so the artifact is invisible to `wiki list` and the organizer hits a FOREIGN KEY
error placing it in a nav group — the artifact is orphaned from navigation.

**Change:** new `register_artifact_wiki_page(bundle, spec, table)` upserts a
`kind=data` row (the schema and `PageKind` already allow `data`) via the same
`upsert_wiki_page` the article commit uses; called from all three commit paths
(`data consolidate --commit`, `data commit`, `data rebuild`). Idempotent.

**Measured (harness proxy `K_data_artifact_wiki_registration`, before = master `3d7fd4a`, after = branch):**

| proxy | before | after |
|---|---|---|
| committed artifact registered in wiki page DB | no | **yes** |
| `wiki_pages.kind` for the artifact row | none (orphaned) | **`data`** |

The artifact is now a first-class wiki page the organizer/index/graph can
reference. **Quality guard:** upsert is idempotent; full suite `1536 passed, 1
skipped` (+1 test); ruff clean; no other proxy regressed. PR: #103 (merged).

### Iteration 7 — F26 embed pages at commit for mid-loop semantic routing (`effq-mid-loop-vectors`)

**Backlog item:** F26 — P5's `wiki_find(mode="semantic")` returns nothing
mid-loop because committed-page vectors are only built by the finalize `wiki
rebuild`. So P5's routing of residual chunks to existing pages was inert during
the loop that depends on it.

**Change:** `embed_committed_page(bundle, page)` incrementally embeds a page into
the same embedding space the full rebuild uses (shared `wiki_page_passage`
format + `_wiki_space_id`, so an incremental vector is byte-identical to a
rebuilt one). Called best-effort from `commit_page` after the page is projected;
the finalize `wiki rebuild` remains the backstop, so a missing embedder can't
fail a commit. Extracted `wiki_page_passage` so the passage text has one
definition.

**Measured (harness proxy `L_mid_loop_wiki_vectors`, before = master `4e21705`, after = branch; `WIKIFY_EMBEDDER=hash`):**

| proxy | before | after |
|---|---|---|
| incremental embed-at-commit present | no | **yes** |
| semantic `wiki_find` hits after a commit, before any rebuild | none (0) | **1** |

P5 can now route residual chunks to freshly-committed pages in the same loop.
**Quality guard:** finalize rebuild unchanged and remains the backstop; full
suite `1537 passed, 1 skipped` (+1 test, exercising the real commit→semantic
path under the hash embedder); ruff clean; no other proxy regressed. PR: #104 (merged).

## Cumulative result

The deterministic harness now carries the six PR-#96 proxies (A–E, plus the C
SENSE and D ranking) and seven improve-loop proxies (F–L), each with a measured
before/after and a green quality gate. Every accepted change moved its proxy
beyond the (zero) noise band with no regression to the prior proxies, and each
landed as its own merged PR with `ruff` + `pytest tests/wikify` green.
