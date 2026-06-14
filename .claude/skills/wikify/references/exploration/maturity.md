# Maturity Score

The investigate workflow uses a deterministic composite score to
decide when a dossier is ready to be written. Implementation:
`src/wikify/bundle/work/maturity.py`. CLI surface:
`wikify work maturity --all --run <bundle> --format json`.

## Bands

A slug is in one of five bands:

| band | meaning |
|---|---|
| `new` | gates failed, score == 0 |
| `growing` | partial coverage, score < threshold |
| `stalled` | gates failed AND growth_stalled true |
| `ready` | gates passed AND score >= threshold |
| `parked` | curator decision; out of consideration |

Promotion threshold: **T = 0.70** for both article and person rules.

## Article rule

### Hard gates (all must hold)

- `has_definition_evidence`: at least one chunk's quote matches the
  definition regex (`is a`, `refers to`, `defined as`, etc.).
- `n_chunks >= 8`: at least 8 active evidence records.
- `n_docs >= 4`: at least 4 distinct doc_ids.
- `growth_stalled`: no `evidence_added` event for this slug in the
  last 2 rounds (or bundle has no `round_started` events). Because this
  is a gate, a fully-evidenced slug scores `0` (band `growing`) on the
  round it is grown and only crosses into `ready` ~2 rounds later, once
  evidence has plateaued. The editor's loop must therefore stop
  touching a saturated slug and let the timer elapse before the WRITE
  wave can fire (see `wikify-investigate/SKILL.md`, WRITE wave). The
  evidence round is derived from the ORDER of `evidence_added` events
  relative to `round_started` markers (the `evidence_added` payload's
  own `round` is not read), so the loop must emit a `round_started`
  (carrying an integer `round`) at the top of each round; absent any
  `round_started` event, `growth_stalled` falls back to `True` and every
  slug reads as `stalled`.

If any gate fails, `score = 0`, `band` is `new` / `growing` /
`stalled` depending on `growth_stalled`.

### Composite (after gates pass)

```
score = 0.25 * min(n_chunks / 12, 1.0)
      + 0.15 * min(n_docs / 6, 1.0)
      + 0.30 * (kinds_present / kinds_required)
      + 0.20 * (1 - chunk_jaccard_with_link_neighbours_max)
      + 0.10 * diversity_bonus
```

Components:

- **n_chunks**: saturates at 12.
- **n_docs**: saturates at 6.
- **kinds_present / kinds_required**: how many stencil kinds are
  detected in the evidence quotes (see Stencils below).
- **chunk_jaccard_with_link_neighbours_max**: max over wiki.db
  depth-1 link neighbours of `|A intersection B| / |A union B|` on
  the chunk_id sets. Catches "we are rewriting an existing page".
  `0` when no neighbours exist.
- **diversity_bonus**: `1 - HHI(per-doc chunk share)`. Penalises
  "10 chunks from 1 doc" without requiring more doc count.

## Kind stencils

Articles pick a stencil that defines `kinds_required`. The notebook
frontmatter carries the chosen stencil in
`maturity.kind_stencil`. Curator can switch the stencil between
rounds when a dossier's content drifts.

| stencil | kinds_required |
|---|---|
| `article-method` (default) | definition, mechanism, application |
| `article-theory` | definition, mechanism, limitation |
| `article-survey` | definition, variant, application |
| `article-history` | definition, variant, limitation |

Each stencil requires 3 kinds. The 30%-weighted kind-coverage term
gives the strongest single signal a dossier has the right shape.

## Person rule (separate)

Gates:

- `n_quoted_contribution_chunks >= 3`: at least 3 chunks whose quote
  carries a contribution verb (`proposed`, `introduced`,
  `developed`, `invented`, `discovered`, `demonstrated`, etc.).
- `n_distinct_docs >= 2`.
- `author_metadata_present`: any alias on the work card starts with
  `author:` (the baseline's convention from
  `corpus_find(rank="author")`).

Composite:

```
score = 0.45 * min(n_quoted_contribution_chunks / 4, 1.0)
      + 0.25 * min(n_distinct_docs / 3, 1.0)
      + 0.15 * has_collaboration_evidence
      + 0.15 * has_temporal_anchor
```

- **has_collaboration_evidence**: any quote matches a collaboration
  pattern (`with`, `co-authored`, `colleagues`, `team`, etc.).
- **has_temporal_anchor**: any quote contains a year (19xx / 20xx).

No "biography" requirement — that's the invention vector the baseline
already guards against.

## Recomputing

Maturity is a pure function of:

- the slug's `evidence.jsonl` (active records only),
- the slug's `notebook.md` frontmatter (for `kind_stencil`),
- the wiki.db link-neighbour chunk sets,
- the events ledger filtered for `round_started` and `evidence_added`
  events on this slug.

It does not depend on the corpus or on prior maturity computations.
Calling `wikify work maturity` is idempotent and cheap (one ledger
scan + one wiki.db query per slug).

The editor recomputes maturity for *touched slugs only* each round;
a `--all` pass at the start of the run and at finalize is sufficient
for snapshot purposes.

## What maturity is NOT

- Not a metric in `wikify eval`. M1/M3/M5/M6 stay the audit signals.
- Not a hard accept/reject; it is a band marker. The editor still
  decides to dispatch the write wave based on band + budget.
- Not a quality score for committed pages. Once a page is committed
  the slug no longer needs maturity — it has wiki.db rows instead.
