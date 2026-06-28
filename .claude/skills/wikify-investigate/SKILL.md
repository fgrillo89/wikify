---
name: wikify-investigate
description: Researcher-style iterative wiki builder. Editor orchestrator dispatches explorer subagents that walk the corpus via named recursive patterns (P1-P5), gather evidence into notebook dossiers, and write pages when a composite maturity score crosses the gate. A DATA wave harvests verifiable numbers/tables into a claim store and consolidates them into evolving kind=data artifact tables. Coverage of the corpus chunk set is the primary objective. Re-entrant on the same bundle when new corpus material arrives.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-investigate

Editor orchestrator that builds a wiki the way a researcher would:
read top papers, anchor concepts, hop chunks, walk citations, sweep
exact terms, gather evidence into per-slug notebooks, and write only
once a composite maturity score passes a gate. The loop runs until the
concept ontology is complete (roster saturated, write queue drained,
coverage plateaued), with its gap-explorer pattern (P5) pushing
`addressable_coverage_ratio` up as a by-product — not until any fixed
coverage fraction, which structural chunks (references, captions,
figures) cap well below 1.0.

The editor runs on a **top-tier model** (e.g. Opus): it owns every
dispatch, kind, merge, park, and stop decision, and it adjudicates
escalations from subagents. Subagents run on cheaper tiers and escalate
out-of-mandate judgements back to the editor rather than guessing
(see Escalation).

The explorer mechanics live in
`../wikify-investigate-explore/SKILL.md` (the recursive pattern
library). The maturity formula lives in
`../wikify/references/exploration/maturity.md`. This skill owns the
editor's loop shape, pattern selection rubric, stop conditions, the
curate phase, and re-entry handling.

## Workflow (per round)

```
SENSE -> DECIDE -> DISPATCH -> CONSOLIDATE -> REASSESS -> [CURATE] -> EMIT -> STOP CHECK
```

### Setup (round 0 only)

Size the run to the corpus first (see Sizing and defaults):

Read `D = n_docs` from `wikify corpus check`, compute `budget_est` from
the Sizing formula (`600_000 * (expected_pages + expected_people)`), then:

```bash
wikify corpus check <corpus> --format json
wikify run init --bundle <bundle> --corpus <corpus> \
  --strategy investigate --target-haiku-eq <budget_est>
```

Bind MCP:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
```

Record the `corpus_fingerprint` from `context_show().health.fingerprint`
into run notes; it gates the re-entry path.

### 1. SENSE

One call returns the whole snapshot:

```bash
wikify run sense --run <bundle> --corpus <corpus> --round <N> --format json
```

It carries `budget` (`target`/`spent`/`remaining` haiku-eq — `spent`
is reconciled from the call ledger, so the STOP-CHECK budget bound is
live), `bands` counts, `concepts` (per-slug `band`, `score`,
`gates_passed`, and a `committed` flag so already-written slugs drop out
of the WRITE wave without a separate `wiki list`), `coverage`
(`chunk_coverage_ratio` raw plus `addressable_coverage_ratio` over
non-structural chunks — the latter is the meaningful coverage signal),
`data` (`n_points`, `verified_ratio`,
subjects/properties — drives the DATA consolidate trigger), and
`committed_pages`. This single read replaces the separate `run show` +
`work maturity --all` + `work coverage` + `data coverage` reads.

Then, if `derived/eval.json` exists, read `M3.g_evidence.modularity`
for the bridge rule; otherwise treat modularity as `null` (bridge does
not fire in round 0).

The `committed` band joins `ready`, `growing`, `stalled`, `new`,
`parked`; slugs flagged `committed` are done and never re-dispatched.

If `corpus_fingerprint` differs from the value last written to
`state.json`, emit `corpus_drift_detected` and force a SEED wave next
round (see Re-entry).

### 2. DECIDE — fixed precedence

Build a dispatch plan that is **slug-disjoint by construction**: at
most one Task per slug per round. Walk the precedence list, attaching
targets to the plan in order, removing them from later bands.

1. **WRITE wave.** Every slug in `ready` band. Up to `wave_size`
   (from Sizing) per round. Eager — writing is terminal. Note the
   readiness lag: `growth_stalled` is a gate, so a well-evidenced slug
   only enters `ready` once NO `evidence_added` event fired for it in
   the last 2 rounds. The rhythm is therefore grow -> leave untouched
   ~2 rounds (grow other slugs meanwhile) -> `ready` -> write. Do not
   keep re-growing a saturated slug or it never becomes writable.
2. **GROW wave.** Every slug in `growing` band (`0.50 <= score < 0.70`)
   with `growth_stalled == False`. Up to `wave_size`, slug-disjoint
   from WRITE. Per-slug pattern selection:
   - notebook has citation anchors in its evidence -> **P2** (citation-walk)
   - notebook has stable aliases (3+) -> **P4** (exact-term sweep) then
     **P3** (semantic-boundary), batched in one Task
   - otherwise -> **P3** alone
3. **BRIDGE wave.** Fires only if `M3.modularity > 0.45` AND a
   sub-median link-weight edge exists in `wiki.db`. One Task on the
   weakest such edge, running P3 over the *union* of the two endpoint
   notebooks' chunk sets. Emits `concept_suggestion` only; never
   appends evidence to either endpoint.
4. **SEED wave.** Fires when `concept_count < target_min` (`target_min`
   from Sizing and defaults) OR every dossier is `ready`/`stalled`.
   Seed from the top-K uncovered PageRank docs, where K is
   `max(target_min - concept_count, wave_size)`. Run SEED as a SINGLE
   **P1** Task over the doc list (not one task per doc): P1 tasks
   *create* slugs, and two docs often yield the same concept, so
   parallel SEED tasks would race on the same slug folder and violate
   slug-disjointness. The single task dedups concept titles internally
   and skips any that match an existing slug.
5. **PERSON wave.** Fires once `concept_count >= target_min/2` (so the
   topical roster exists first) and only until `person_count >=
   expected_people` (a small VIP quota — see Sizing). Seed from the top
   authors by the strongest populated `rank_metrics.author` metric
   (`h_index`, else `citation_count`, else `n_papers`), gated to genuine
   VIPs: take only authors above the corpus median on that metric, cap
   at `expected_people`. For each, one concept:
   `wikify work add concept "<Display Name>" --kind person --aliases
   '["author:<key>"]'`, `notebook-init`, then `build-evidence` (the
   person path gathers quoted-contribution chunks). The person maturity
   gate is strict by design (>= 3 quoted-contribution chunks from >= 2
   docs + `author:` alias), so thinly-covered authors never reach
   `ready` and silently drop out — this is the regulariser that keeps
   people pages few and VIP-only. Run as a SINGLE Task over the author
   list (same slug-race reasoning as SEED).
6. **GAP wave.** Fires every round, low cost. One **P5** Task on the
   top 20 uncovered chunks by PageRank.
7. **DATA wave.** Fires every round, low cost. Owned by the data skills,
   not the P1-P5 explorer. Two parts:
   - **Harvest.** One `wikify-extract-data` Task (pattern label `P6`,
     stage `data`). Dedicated pass over the same top uncovered PageRank docs
     this round's SEED/GAP touch — their tables (`asset_type='table'`) and
     number-dense chunks, which the P1-P5 explorers deliberately skip — plus
     a piggyback over any slug grown this round. It stages points and runs
     `wikify data add` (the verification gate).
   - **Consolidate.** When `data coverage` shows a ripe theme (>= 4 subjects
     sharing a property, not yet covered by a committed artifact), dispatch
     one `wikify-consolidate-data` Task to build + commit a `kind=data`
     artifact. At most one consolidation Task per round.

**Anti-starvation slack.** If the loop would otherwise stop (`STOP
CHECK` would fire) AND SEED or GAP would still produce work, dispatch
one half-size SEED+GAP wave before terminating.

### 3. DISPATCH

For each plan entry, spawn one `Task` (sonnet tier) bound to
`wikify-investigate-explore` for explore Tasks or `wikify-write-page`
for the write wave. Pass `pattern`, `target`, `budget_chunks`, `depth`
verbatim from the plan. Record `{role, model_id, tier, tokens_in,
tokens_out, stage}` from each return. Use the harness-measured token
usage reported at the Task boundary (`subagent_tokens`), not the
subagent's self-reported `tokens_in/tokens_out` — children cannot
introspect their own tool-result intake and routinely undershoot it by
several fold.

Before dispatching the first Task of each wave, emit one
`pattern_dispatched` event per Task:

```
wikify run record-event --type pattern_dispatched \
  --stage explore --concept-id memristor --run <bundle> \
  --data '{"pattern": "P3", "target": "memristor", "depth": 0, "budget_chunks": 30}'
```

`record-event` reads the payload from `--data` (JSON object); pass
`--from-stdin` only when you deliberately pipe the payload. Each round
MUST emit `round_started` (`--data '{"round": N}'`) BEFORE that round's
explore/write Tasks, and in CONSOLIDATE one `evidence_added`
(`--concept-id <slug>`) per slug that gained evidence. `_growth_stalled`
(and thus the maturity gate) derives a slug's last-evidence round from
the ORDER of its `evidence_added` events relative to `round_started`
markers, so emission order is what matters — the `evidence_added`
payload's own `round` is not read and is optional. `round_started`,
`round_completed`, and `pattern_dispatched` ARE rejected without a
non-negative integer `round`. `work add evidence --round N` emits the
`evidence_added` event for you.

Stages: `explore` for P1-P5 waves, `write` for the write wave, `data` for
the DATA wave (harvest + consolidate). DATA-wave Tasks bind to
`wikify-extract-data` and `wikify-consolidate-data`.

### 4. CONSOLIDATE

```bash
wikify work tend --run <bundle>
wikify run record-calls --from-stdin --run <bundle> --format json <<'EOF'
{"role":"explorer","model_id":"...","tier":"M","tokens_in":N,"tokens_out":N,"stage":"explore"}
...
EOF
```

P5 produces `evidence_suggestion` and `concept_suggestion` inbox
records; `work tend` consolidates them. P1 may also append concept
suggestions. `work tend` promotes a `concept_suggestion` carrying
`"origin": "gap_explorer"` to a concept folder only once its title is
backed by >= 2 distinct supporting chunks (a one-off gap proposal is
retained in the inbox and accumulates across rounds, capped); a
deliberate concept added via `work add feedback concept` (origin not
`gap_explorer`) is promoted immediately. This keeps the roster from
filling with evidence-less stubs that would keep the SEED wave firing
on phantom concepts.

**Adjudicate escalations.** For each Task that returned an `escalate`
block, the editor decides now (it is top-tier) and encodes the ruling:
create / merge / park the slug, route the evidence, or adjust the
`kind_stencil`. If the ruling changes a target, queue one focused
follow-up Task for next round with the decision baked into its target
spec. Never carry an unresolved escalation past CONSOLIDATE.

### 5. REASSESS

Recompute maturity for *touched slugs only* (slugs whose
`evidence.jsonl` or notebook changed this round). Cheap: one CLI call
per slug or a single `--all` call.

Recompute M1 only on rounds where at least one page committed (M1
cannot move otherwise; saves the chunk-embedding pass). Coverage is
cheap; recompute every round.

### 6. CURATE (every `curate_every = 2` rounds)

No subagents. In-editor:

1. **Dedup — the orchestrator adjudicates (see Dedup rulebook).** A
   lexical title check alone misses semantically-redundant pairs
   (`Memristance` vs `Memristor`), so the editor — top-tier and already
   holding the roster — makes the call. Surface candidate pairs with
   `wikify work cluster-concepts --by evidence --run <bundle> --format
   json` (Jaccard over evidence doc-sets; person concepts are clustered
   separately). For every pair sharing a cluster, or whose normalised
   titles are near-duplicates, apply the Dedup rulebook and either merge
   or keep distinct. Resolve `work/inbox/concept_suggestions.jsonl`
   survivors the same way before they are promoted.
2. For each slug, re-check the `kind_stencil` choice against the
   notebook body. A drift signal: notebook accumulates `variant`
   evidence -> consider switching from `article-method` to
   `article-survey`; switching loosens kind-coverage requirements.
3. Park slugs that have stalled with too little evidence (`band =
   stalled, n_chunks < 6` for two consecutive curate cycles). Emit
   `dossier_parked` event. Parked slugs do not block stop conditions.

### 7. EMIT

```
wikify run record-event --type round_completed --stage round \
  --run <bundle> \
  --data '{"round": N, "band_counts": {...}, "M1": 0.18,
           "M3_modularity": 0.41, "chunk_coverage_ratio": 0.42,
           "dispatched_modes": ["GROW", "GAP"],
           "dispatched_patterns": ["P3", "P5"], "budget_used": ...}'
```

### 8. STOP CHECK

Stop when ALL completeness signals hold:

- **Roster saturated.** No new `concept_suggestion` for 2 rounds (P5
  emits only `evidence_suggestion`s), or `concept_count` flat for 2 rounds.
- **Write queue drained.** No `ready` slug is unwritten.
- **Coverage plateau.** `delta_coverage_per_round < 0.01` for 2 rounds
  AND no dossier crossed the promotion threshold in those rounds.

Or stop early on ANY soft ceiling:

- `addressable_coverage_ratio >= 0.33`
- `chunk_coverage_ratio >= 0.25`
- `spent_haiku_eq >= target_haiku_eq`
- `rounds >= max_rounds`
- No candidate action fires after the anti-starvation slack.

Otherwise increment round and re-enter SENSE.

## Re-entry on existing bundles

When invoked on a bundle that already has `round_completed` events:

1. Read the latest `round_completed` snapshot; resume the round
   counter from there.
2. Re-bind MCP and compare `context_show().health.fingerprint` against
   `state.json.corpus_fingerprint` (set during run init).
3. If equal -> jump straight into SENSE and re-enter precedence as if
   mid-loop. Typically one CURATE pass and stop with
   `no_candidate_action`.
4. If different:
   - Emit `corpus_drift_detected` with old + new fingerprints.
   - Compute `new_doc_ids = corpus_doc_ids - union(notebook.covered_docs)`.
   - For each new doc, run a one-shot haiku Task asking "does this doc
     materially overlap any existing dossier slug?" with the dossier
     index summary as context. Append `new_doc_action_needed=true` to
     any matched dossier (via a CLI subcommand or direct yaml edit
     through `notebook` helpers).
   - Queue unmatched docs for the next SEED wave (P1 on those docs).
   - After the new docs are absorbed, re-stamp the stored fingerprint
     so drift does not re-fire next round:
     `wikify run set --corpus-fingerprint <new> --run <bundle>` (the
     `<new>` value is `context_show().health.fingerprint`).

## Finalize (after STOP)

Same close-out as baseline P5 plus chunk-coverage capture:

```bash
wikify work tend --run <bundle>
wikify data rebuild --run <bundle>   # refresh every committed data artifact
wikify wiki check --run <bundle>
wikify wiki rebuild --run <bundle>
wikify wiki navigation-context --run <bundle> \
  --out <bundle>/derived/navigation_context.json
# Invoke wikify-organize-wiki to write derived/navigation.json.
wikify render --bundle <bundle> --format html
wikify run close --status completed --run <bundle>
wikify eval --bundle <bundle> --corpus <corpus>
wikify work coverage --run <bundle> --corpus <corpus> --format json \
  > <bundle>/derived/coverage.json
```

Then run the Inspection Loop and write the Final Report.

## Subagent contracts

The editor (this skill's main loop) runs at tier **L** (top-tier). All
roles below are subagents it dispatches; each may add an optional
`escalate` block to its return when a decision exceeds its mandate
(see Escalation).

| role | tier | skill | inputs | return |
|---|---|---|---|---|
| editor | **L** | this skill (main loop) | bundle, corpus | round events, dispatch plan, escalation rulings |
| explorer | sonnet M | `wikify-investigate-explore` | `pattern`, `target`, `run`, `corpus`, `budget_chunks`, `depth` | per-target envelope (see explorer skill) |
| classifier | haiku S | this skill (Re-entry) | `doc_id`, dossier index | `{overlapping_slugs: [...]}` |
| writer | sonnet M | `wikify-write-page` | `slug`, dossier path, evidence path | response.json path |
| data-extractor | sonnet M | `wikify-extract-data` | `target` (docs or slug), `run`, `corpus` | `{submitted, stored, rejected}` |
| data-consolidator | sonnet M | `wikify-consolidate-data` | `run`, theme (properties) | committed artifact id |
| organizer | sonnet M | `wikify-organize-wiki` | navigation context | navigation.json |

Every Task return must yield `{tokens_in, tokens_out, model_id}` for
the Telemetry pass below, plus an optional `escalate` block.

## Escalation (subagent -> editor)

Any subagent that hits a decision **outside its mandate** returns an
`escalate` block instead of guessing, e.g.:

```json
"escalate": {"question": "new concept or evidence for 'Atomic Layer Deposition'?",
             "context": "chunk:3ce6__c0007 frames the TiN/HfO2 stack as a distinct device",
             "options": ["new_concept", "evidence_for:atomic-layer-deposition", "drop"]}
```

The top-tier editor adjudicates in CONSOLIDATE: it encodes the decision
(create / route / merge / park / adjust `kind_stencil`) and, if that
changes a target, re-dispatches one focused Task next round. Escalate —
never silently pick — on concept-vs-evidence routing, kind/stencil
choice, near-duplicate merges, or slug create/destroy. Routine
accept/reject of a chunk is the subagent's own job. (Distinct from the
writer's validator-retry **tier** escalation in
`../wikify/references/writing/escalation.md`, which just re-runs at a
higher tier.)

## Dedup rulebook

The editor decides merges; these are the signals and the action. Run it
in CURATE over the candidate pairs `cluster-concepts --by evidence`
surfaces (plus any near-duplicate titles).

**Signals (cheap, no chunk-text reads):**

- **Evidence overlap** — Jaccard of the two concepts' evidence doc-sets
  from `cluster-concepts`. High overlap (>= 0.5) is a strong merge
  signal; the two dossiers are built from the same papers.
- **Semantic proximity** — `wiki_find`/`corpus_find` on one title
  returns the other in its top hits (the editor may read titles +
  previews, never chunk bodies).
- **Subsumption** — one title is a property / part / instance / acronym
  of the other (`Memristance` is the defining property of `Memristor`;
  `RRAM` ↔ `Resistive Random-Access Memory`). This is the case lexical
  distance misses.
- **Lexical** — near-identical normalised titles (plural/singular,
  hyphenation): always merge the variant into the base.

**Decision:**

- **Merge** when evidence overlap is high AND (semantic proximity OR
  subsumption OR lexical match). Pick the canonical slug = the broader /
  more-cited concept (`Memristor` over `Memristance`); the narrower
  becomes an alias.
- **Keep distinct** when the pair shares sources but covers genuinely
  separate facets a reader would want apart (e.g. `Bipolar Resistive
  Switching` vs `Non-Filamentary Resistive Switching` — different
  mechanisms). Sharing evidence is not sufficient; demand redundancy of
  the *concept*, not just the sources.
- **When unsure, keep distinct** — a wrong merge is lossy and hard to
  undo; a missed merge is cheap to catch next CURATE.

**Execute a merge of `<dup>` into `<canonical>` (both in-flight):**

```bash
wikify work seen-chunks <dup> --run <bundle> \
  | jq '[.seen_chunk_ids[] | {chunk_id: .}]' \
  | wikify work build-evidence <canonical> --from-ids @- \
      --corpus <corpus> --run <bundle>            # fold evidence (dedups)
wikify work set <canonical> --aliases '[<existing>, "<Dup Title>", <dup aliases>]' \
  --run <bundle>                                   # keep the dup title resolvable
wikify work set <dup> --status merged --run <bundle>   # tombstone (drops from roster)
wikify run record-event --type concept_status_changed --concept-id <dup> \
  --run <bundle> --data '{"status": "merged", "into": "<canonical>"}'
```

A `merged` (also `parked`, `dropped`) card never re-enters `ready` /
`growing`, so WRITE/GROW skip it and it does not hold the stop check
open. **If either page is already committed**, do NOT hand-edit — run
`wikify-refine` to redirect/fold (committed pages are repaired only
through refine).

## Sizing and defaults

Round-level knobs scale with corpus size; per-Task depth is fixed. At
setup read `D = health.n_docs` and `Kc = health.n_chunks`, then derive
(`clamp(x,lo,hi) = max(lo, min(hi, x))`):

```
wave_size            = clamp(ceil(D / 80), 2, 12)
target_min           = clamp(round(42 * log10(D) - 27), 10, 200)        # SEED concept floor, ~log(D)
concurrent_explorers = clamp(wave_size, 2, 8)                           # throttled by live rate limits
max_rounds           = clamp(round(Kc / (wave_size * 25)) + 12, 12, 250) # coverage-bound safety ceiling
expected_pages       = clamp(round(38 * log10(D) - 37), 5, 250)        # article roster the build commits, ~log(D)
expected_people      = clamp(round(4 * log10(D) - 3), 0, 30)           # VIP person pages, median-author gated; few by design
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
is a small VIP quota on top (median-author gated, then filtered hard by
the strict person maturity gate, so committed people are usually fewer).
`budget_est` is driven by total committed pages, not chunks: writers are
~75% of spend at ~0.6M haiku-eq per page (writer + amortized
explore/data); person pages cost about the same per page. It excludes
the editor, which runs inline and is not recorded in `spent_haiku_eq`.
Scale by the observed roster on diverse corpora, which commit more pages
per doc than a focused one.

### Coverage targets (soft ceilings)

Coverage is a **stop ceiling, not the objective** — completeness is.
Two ratios, both in `run sense`'s `coverage` block:

- `addressable_coverage_ratio` — covered chunks / **non-structural**
  chunks. The denominator drops references, captions, figures, tables,
  acknowledgments, appendix, and boilerplate (the explorer's
  `excluded_kinds`), which on a typical parsed-paper corpus are ~half of
  all chunks. This is the meaningful number. **Target: 0.33.**
- `chunk_coverage_ratio` — covered chunks / **all** chunks. Kept for
  continuity and the per-round table. **Raw ceiling: 0.25.** A raw ratio
  near 1.0 is structurally impossible (you would have to cite caption
  and reference-list chunks).

Both scale with corpus diversity, not paper count: a topically broad
corpus supports more distinct concepts and reaches higher; a redundant
one plateaus lower. Let the completeness signals fire first; treat these
as backstops.

### Fixed per-Task knobs
- Explorer budget per Task: `budget_chunks = 30`, `depth = 2` (P1),
  `depth = 1` (P2). `curate_every = 2`.
  `addressable_coverage_target = 0.33`, `coverage_target = 0.25` (raw).
- Editor tier: **L (top-tier, e.g. Opus)**. Explorer M. Writer M.
  Classifier S. Claim owner `investigate`, TTL 1800 s.

### Interruptions and re-entry

A large build may span several rate-limit windows. Each round ends with
a `round_completed` checkpoint, so an interruption costs at most the
in-flight round; re-invoke on the same bundle to resume from the last
checkpoint (see Re-entry). Evidence persists on disk, so coverage is
monotonic across windows.

## Hard Rules

- **One Task per slug per round.** The dispatch plan is slug-disjoint.
  Never spawn two waves that share a slug.
- **The editor never reads chunk text.** It reads slug-level
  summaries, scores, and event envelopes. Explorers do all chunk
  reading.
- **Editor is top-tier; subagents escalate, don't guess.** Run the
  editor on the strongest model (e.g. Opus); subagents return an
  `escalate` block on out-of-mandate calls (see Escalation) rather than
  resolving them silently.
- **Do not bypass the maturity gate.** A slug that has not crossed
  T = 0.70 does not get written. If a curator wants to promote a
  stalled slug, change its `kind_stencil` (which may loosen the kind
  requirement) — do not edit the score directly.
- **Do not repair committed pages with ad hoc scripts.** Use
  `wikify-refine` for that.
- **Cost curves are invalid without `type="call"` events.** Always
  record per Task.

## Inspection Loop

After render, inspect at least 5 pages, prioritising ones promoted by
investigate over baseline:

| page kind | what to check |
|---|---|
| ready -> committed in late rounds | citation depth, evidence kind coverage |
| stalled / parked | did the editor's park decision look right? |
| bridge-emitted concept | does it actually bridge the two endpoints? |
| person page | quoted contributions, temporal anchors, no biography invention |
| chunk-residual map | which corpus regions are still uncovered |

## Final Report (checklist)

- [ ] Bundle + corpus + `corpus_fingerprint` (and any drift)
- [ ] Total rounds, stop reason, dispatched_patterns histogram
- [ ] Committed / failed article + person pages
- [ ] Per-round table: band counts, M1, M3, `chunk_coverage_ratio`,
      budget_used
- [ ] Rendered `index.html` path, eval report path, coverage.json path
- [ ] Per-page evidence stats (active records, distinct docs,
      kinds_present)
- [ ] Eval metrics M1, M3, M5, M6; GT-P / GT-C availability
- [ ] Call-cost telemetry status (must be non-empty per wave)
- [ ] Qualitative site judgment; deterministic blockers; next fixes

## References

- `../wikify-investigate-explore/SKILL.md`
- `../wikify-search-corpus/SKILL.md`
- `../wikify-search-wiki/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-gather-evidence-cluster/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify/references/exploration/patterns.md`
- `../wikify/references/exploration/maturity.md`
- `../wikify/references/exploration/workflow-contracts.md`
- `../wikify/references/writing/escalation.md`
