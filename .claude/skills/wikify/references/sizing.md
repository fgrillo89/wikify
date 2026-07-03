# Sizing and defaults

Round-level knobs scale with corpus size; per-Task depth is fixed. At
setup read `D = health.n_docs` and `Kc = health.n_chunks`, then derive
(`clamp(x,lo,hi) = max(lo, min(hi, x))`):

```
wave_size            = clamp(ceil(D / 80), 2, 12)
target_min           = clamp(round(42 * log10(D) - 27), 10, 200)        # SEED concept floor, ~log(D)
concurrent_explorers = clamp(wave_size, 2, 8)                           # throttled by live rate limits
max_rounds           = clamp(round(Kc / (wave_size * 25)) + 12, 12, 250) # coverage-bound safety ceiling
expected_pages       = clamp(round(38 * log10(D) - 37), 5, 250)        # article roster the build commits, ~log(D)
expected_people      = clamp(round(4 * log10(D) - 3), 0, 30)           # SOFT person-page target (may be exceeded, see below); median-author gated
person_quota_multiplier = 2.0                                          # review up to 2x expected_people candidates; maturity gate still decides commits
budget_est_haiku_eq  = 600_000 * (expected_pages + expected_people)    # ~0.6M haiku-eq/committed page; editor is inline, off the call ledger
```

| corpus (~18 chunks/doc) | wave_size | target_min | expected_pages | people | max_rounds | budget est |
|---|---|---|---|---|---|---|
| 15 docs   | 2  | 22  | 8   | 2  | 17  | ~6M   |
| 100 docs  | 2  | 57  | 39  | 5  | 48  | ~26M  |
| 500 docs  | 7  | 86  | 66  | 8  | 63  | ~44M  |
| 1000 docs | 12 | 99  | 77  | 9  | 72  | ~52M  |
| 10k docs  | 12 | 141 | 115 | 13 | 250 | ~77M  |
| 100k docs | 12 | 183 | 153 | 17 | 250 | ~102M |
| 1M docs   | 12 | 200 | 191 | 21 | 250 | ~127M |

Rows past ~1k docs assume a **single coherent domain**: distinct concepts
keep saturating ~log(D) while the extra papers pile up as redundant
evidence, so `expected_pages` (and cost) grows slowly. A multi-domain
corpus at that scale is not one ontology — shard it by sub-domain, run
one bundle each, and the budget is the per-shard estimate times the
shard count. At 10k+ docs the build also spans many rate-limit windows;
re-entry, not budget, is the practical throttle (see Interruptions).

`budget_est`, `max_rounds`, and the coverage targets are **non-binding
ceilings, not targets** — the loop stops first on completeness (roster
saturation + drained write queue + coverage plateau; see STOP CHECK), or
on a MAX plan, rate limits.
`target_min` is the SEED floor only and grows ~log(D) because distinct
concepts saturate far below paper count; concepts past it emerge from
P5 coverage, not seeding. `expected_pages` is a separate ~log(D) fit for
the article roster the build actually commits, calibrated to observed
runs and independent of the `target_min` SEED floor. `expected_people`
is a **soft target**, not a hard cap: it may be exceeded for
source-critical authors (on `>= 2` committed article evidence documents,
or one high-centrality source). Review up to `person_quota_multiplier`
(2.0) times `expected_people` candidates; the strict person maturity gate
still decides which commit, so it stays the quality regulariser. People
are seeded not only from top-metric VIPs but also from the **authorship
of already-cited article source documents** and their close (co-author
distance `<= 1`) collaborators, so the researchers a wiki actually leans
on get pages.
`budget_est` is driven by total committed pages, not chunks: writers are
~75% of spend at ~0.6M haiku-eq per page (writer + amortized
explore/data); person pages cost about the same per page. It excludes
the editor, which runs inline and is not recorded in `spent_haiku_eq`.
Scale by the observed roster on diverse corpora, which commit more pages
per doc than a focused one.

## Coverage targets (soft ceilings)

Coverage is a **stop ceiling, not the objective** — completeness is.
Two ratios, both in `run sense`'s `coverage` block:

- `addressable_coverage_ratio` — covered chunks / **non-structural**
  chunks. The denominator drops references, captions, figures, tables,
  acknowledgments, appendix, and boilerplate (the explorer's
  `excluded_kinds`), which on a typical parsed-paper corpus are ~half of
  all chunks. This is the meaningful number. **Target: 0.33.**
- `chunk_coverage_ratio` — covered chunks / **all** chunks. Kept for
  continuity and the per-round table. **Raw ceiling: 0.25.** A raw ratio
  near 1.0 is structurally impossible: caption, reference-list, and
  other structural chunks are never cited as evidence, so the loop is
  governed by completeness, not a chunk-coverage target near 0.90.

Both scale with corpus diversity, not paper count: a topically broad
corpus supports more distinct concepts and reaches higher; a redundant
one plateaus lower. Let the completeness signals fire first; treat these
as backstops.

## Fixed per-Task knobs

- Explorer budget per Task: `budget_chunks = 30`, `depth = 2` (P1),
  `depth = 1` (P2). `curate_every = 2`.
  `addressable_coverage_target = 0.33`, `coverage_target = 0.25` (raw).
- Editor tier: **L (top-tier, e.g. Opus)**. Explorer M. Writer M.
  Classifier S. Claim owner `investigate`, TTL 1800 s.

## Interruptions and re-entry

A large build may span several rate-limit windows. Each round ends with
a `round_completed` checkpoint, so an interruption costs at most the
in-flight round; re-invoke on the same bundle to resume from the last
checkpoint (see Re-entry). Evidence persists on disk, so coverage is
monotonic across windows.
