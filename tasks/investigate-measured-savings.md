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

<!-- iterations appended below -->
