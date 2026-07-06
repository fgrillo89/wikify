---
name: wikify
description: Researcher-style iterative wiki builder. Editor orchestrator dispatches explorer subagents that walk the corpus via named recursive patterns (P1-P5), gather evidence into notebook dossiers, and write pages when a composite maturity score crosses the gate. A DATA wave harvests verifiable numbers/tables into a claim store and consolidates them into evolving kind=data artifact tables. Coverage of the corpus chunk set is the primary objective. Re-entrant on the same bundle when new corpus material arrives.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify

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
`subskills/explore/SKILL.md` (the recursive pattern
library). The maturity formula lives in
`subskills/reference/references/exploration/maturity.md`. This skill owns the
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
subjects/properties — drives the DATA consolidate trigger),
`committed_pages`, and — computed deterministically from `n_docs` so a
resuming editor never has to re-derive Sizing from prose — `sizing`
(`target_min`, `expected_pages`, `expected_people`, `wave_size`,
`max_rounds`), `roster` (`active_concepts`, `n_committed_articles`,
`n_people`), and `waves` (`seed_should_fire`, `seed_deficit`,
`person_gate_open`, `person_should_fire`, `person_deficit`,
`roster_saturated`). Read `waves` directly in DECIDE and STOP CHECK: it
is the authoritative SEED/PERSON eligibility and roster-saturation signal,
so the roster cannot silently freeze below the SEED floor across a
stateless re-entry. It replaces separate `run show` + `work maturity
--all` + `work coverage` + `data coverage` reads.

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
   (from Sizing) per round. Eager — writing is terminal. Ready slugs may
   be grouped into fewer writer Tasks (one Task processes several ready
   slugs sequentially) to amortise per-agent overhead, as long as the
   Tasks stay slug-disjoint and each slug gets its own `response.json` +
   `draft check` (see DISPATCH). Note the
   readiness lag: `growth_stalled` is a gate, so a well-evidenced slug
   only enters `ready` once NO `evidence_added` event fired for it in
   the last 2 rounds. The rhythm is therefore grow -> leave untouched
   ~2 rounds (grow other slugs meanwhile) -> `ready` -> write. Do not
   keep re-growing a saturated slug or it never becomes writable.
   **Evidence-recall gate (before writing).** Before dispatching a WRITE
   for a ready slug, run `wikify work concept-recall <slug> --corpus
   <corpus> --run <bundle> --format json` and read its `recall` block. If
   `recall.recall_ok` is false AND the gather did not report
   `stop_reason: "pool_exhausted"` (a genuinely mined-out slug), DEFER the write and
   dispatch a targeted GROW that pulls `recall.missing_docs` (the corpus's
   most-relevant papers this page skips), fills any `recall.empty_buckets`
   (a missing era), and reduces `recall.max_doc_share` (over-concentration
   on one doc). Pull those `recall.missing_docs` DIRECTLY, not by generic re-search: for each missing doc run `corpus find --in-doc <doc>` (scored for the concept) to get its best chunks and route them through the vetter, or `wikify work notebook-init <slug> --seed-docs '[<missing ids>]'` then `wikify work build-evidence <slug>` -- capped to the named docs -- so the specific papers the gate identified are retrieved deterministically. Then record the clearance so commit can enforce it:
   `wikify run record-event --type page_recall_cleared --stage write
   --concept-id <slug> --run <bundle> --data '{"recall_ok": true}'` (or
   `{"exhausted": true}`). Only write once `recall.recall_ok` OR the sweep
   is `pool_exhausted` — the article analogue of the DATA-wave recall gate.
   Finalize this loop's article commits with `wikify draft finalize <slug>
   --require-recall` — it refuses to commit an article without a FRESH
   `page_recall_cleared` record (recall_ok or exhausted, and no evidence
   added to the slug since the clearance). This enforces the gate for the
   wikify loop, which ALWAYS passes the flag; a bare `draft finalize`
   without it is intentionally not gated (ad-hoc / test callers). Record
   the clearance AFTER the last GROW for the slug so it is not stale.
   This is what makes a committed page represent the corpus's most diverse,
   relevant evidence rather than whatever few docs seeded it; the writer
   validator's `evidence_underuse` warning is the complementary check that
   the writer then actually cites that breadth.
2. **REFINE wave.** Fires when `wikify work refine-candidates` returns
   candidates. That command now surfaces two signals: a committed page
   whose live evidence outgrew its write-time snapshot, and a committed
   page a newly-relevant committed data artifact postdates (reason
   `new_data`). Dispatch at most `min(2, wave_size)` refine
   Tasks per round (bound to the `refine` subskill), one per top
   candidate by ratio; slug-disjoint from all other waves by
   construction (refine targets committed slugs, which WRITE/GROW never
   touch). A refined page converges: its fresh `page_committed` event
   resets its own baseline and records the artifact, so it won't
   re-trigger until it grows again.
3. **GROW wave.** Every slug in `growing` band (`0.50 <= score < 0.70`)
   with `growth_stalled == False`. Up to `wave_size`, slug-disjoint
   from WRITE. Per-slug pattern selection:
   - notebook has citation anchors in its evidence -> **P2** (citation-walk)
   - notebook has stable aliases (3+) -> **P4** (exact-term sweep) then
     **P3** (semantic-boundary), batched in one Task
   - otherwise -> **P3** alone
4. **BRIDGE wave.** Fires only if `M3.modularity > 0.45` AND a
   sub-median link-weight edge exists in `wiki.db`. One Task on the
   weakest such edge, running P3 over the *union* of the two endpoint
   notebooks' chunk sets. Emits `concept_suggestion` only; never
   appends evidence to either endpoint.
5. **SEED wave.** Fires when `waves.seed_should_fire` (i.e.
   `roster.active_concepts < sizing.target_min`) OR every dossier is
   `ready`/`stalled`. This is not optional while the roster is below the
   SEED floor: a `WRITE`+`GROW`-only round that leaves `seed_should_fire`
   true is the degenerate loop that freezes the roster far below
   `expected_pages` and never opens the PERSON gate. Seed from the top-K
   uncovered PageRank docs, where K is `max(waves.seed_deficit,
   wave_size)`. Run SEED as a SINGLE
   **P1** Task over the doc list (not one task per doc): P1 tasks
   *create* slugs, and two docs often yield the same concept, so
   parallel SEED tasks would race on the same slug folder and violate
   slug-disjointness. The single task dedups concept titles internally
   and skips any that match an existing slug.
6. **PERSON wave.** Fires once `waves.person_gate_open` (i.e.
   `roster.active_concepts >= sizing.target_min/2`, so the topical roster
   exists first); `waves.person_should_fire` additionally confirms the
   people roster is still below the review quota. A run that reaches
   completeness with `roster.n_people == 0` while `person_gate_open` is
   true has skipped this wave — that is a bug, not a saturated roster.
   `expected_people` is a SOFT target, not
   a hard cap (see Sizing): keep seeding while good candidates remain,
   reviewing up to `person_quota_multiplier` (2.0) times `expected_people`.
   Seed from TWO sources: (a) the top authors by the strongest populated
   `rank_metrics.author` metric (`h_index`, else `citation_count`, else
   `n_papers`) above the corpus median; and (b) the **authorship of
   already-cited article source documents** and their close (co-author
   distance `<= 1`) collaborators — the researchers the wiki actually
   leans on, even when below the VIP metric. For each, one concept:
   `wikify work add concept "<Display Name>" --kind person --aliases
   '["author:<key>"]'`, `notebook-init`, then `build-evidence` (the
   person path gathers BOTH quoted-contribution and `identity_context`
   chunks — affiliation/role/career — so the page can lead with who the
   person is). The strict person maturity gate (>= 3 quoted-contribution
   chunks from >= 2 docs + `author:` alias) still decides commits, so
   thinly-covered authors drop out — it is the quality regulariser, not a
   headcount cap. Run as a SINGLE Task over the author list (same
   slug-race reasoning as SEED).
7. **GAP wave.** Fires every round, low cost. One **P5** Task on the
   top 20 uncovered chunks by PageRank. Beyond *coverage* gaps, the P5
   explorer also surfaces **knowledge gaps** it reads in those chunks —
   open questions, contradictory reports between sources, understudied
   materials/conditions — and records each via `wikify work add-gap-note`
   (which quote-verifies the anchor and appends a schema line to
   `work/notes/literature_gaps.md`; the explorer has `Bash(wikify *)`
   access, so it writes the note itself — the editor does not). These
   accumulate across rounds and are synthesized at Finalize. It NEVER
   invents an open question and never infers one from absent coverage: it
   records only ones a chunk states, or a genuine contradiction between two
   cited chunks. This is a first-class objective — what the corpus has NOT
   settled — not a byproduct of coverage.
8. **DATA wave.** Fires every round, low cost. Owned by the data skills,
   not the P1-P5 explorer. Two parts:
   - **Harvest.** One `extract-data` Task (pattern label `P6`,
     stage `data`). Dedicated pass over the same top uncovered PageRank docs
     this round's SEED/GAP touch — their tables (`asset_type='table'`) and
     number-dense chunks, which the P1-P5 explorers deliberately skip — plus
     a piggyback over any slug grown this round. It stages points and runs
     `wikify data add` (the verification gate). When a property becomes a
     consolidation theme (a table is about to be built for it, e.g.
     growth-per-cycle), FIRST run a property-targeted exhaustive harvest:
     `wikify data harvest-property --property <p> --alias ... --unit ...
     --corpus <corpus> --run <bundle>` sweeps the WHOLE corpus (not just
     this round's docs) for every chunk carrying a value for that property,
     and the `extract-data` Task extracts + verifies every candidate via
     `data add`. Aim for `data_recall >= 0.90`; re-sweep across rounds
     while `truncated` or recall stays low. This is what makes a table like
     GPC comprehensive (nearly every ALD paper reports one) instead of
     sparse.
   - **Consolidate.** Each round run `wikify data coverage` and enumerate
     ALL uncovered ripe themes (>= 4 subjects sharing a property with no
     committed artifact). Dispatch a `consolidate-data` Task for each,
     highest-subject-count first, capped at 2 per round; keep dispatching
     across rounds until no uncovered ripe theme remains. Consolidation is
     not optional — do not skip the DATA-consolidate step while a ripe theme
     is uncovered. Commit property tables with `--require-recall` (the
     `consolidate-data` Task passes it): the CLI then reads the
     `property_sweeps` record and REFUSES a sparse table when
     `docs_mentioning_property >= 10` AND `data_recall < 0.75` (or no sweep
     exists), so the editor must loop back to `harvest-property` + extract
     until `>= 0.90` before it can commit. After
     a `consolidate-data` Task commits a new `kind=data`
     artifact, the committed pages it covers become `refine-candidates`
     (reason `new_data`) so the REFINE wave re-drafts them to cite the new
     table under "Related data".

**Anti-starvation slack.** If the loop would otherwise stop (`STOP
CHECK` would fire) AND SEED or GAP would still produce work, dispatch
one half-size SEED+GAP wave before terminating.

### 3. DISPATCH

For each plan entry, spawn one `Task` (sonnet tier) bound to
`explore` for explore Tasks or `write-page`
for the write wave. Pass `pattern`, `target`, `budget_chunks`, `depth`
verbatim from the plan. Record `{role, model_id, tier, tokens_in,
tokens_out, stage}` from each return. `budget_chunks` is NOT a flat 30:
compute it from Sizing (`clamp(round(20 + 6 * log10(D)), 20, 60)`) and
multiply by ~1.5 for a central concept (top decile PageRank/degree)
before passing it, so a larger corpus and a hub concept get a deeper
sweep (the pattern-defaults in `explore` are the floor, the editor scales
up).

**Brief-first, cache-aligned dispatch.** Each subagent's FIRST read is
its stable role brief -- `subskills/write-page/references/writer-brief.md`
for writers, `subskills/explore/references/explorer-brief.md` for
explorers -- not the
full source file set. The brief text is identical across same-role
Tasks, so dispatch all same-role Tasks of a wave in ONE burst; the
shared brief prefix then stays inside the prompt-cache TTL and is
charged once, not per agent. A writer Task may also process MULTIPLE
ready slugs sequentially in one Task to amortise per-agent fixed
overhead, provided the batch is slug-disjoint from every other
concurrent Task (the one-writer-per-slug ledger claim holds at the SLUG
level, not the Task level) and the Task writes each slug's own
`response.json` and runs `wikify draft check <slug> --run <bundle> --dry-run` per slug.
A batched writer processes its slugs INDEPENDENTLY and returns an ARRAY
of per-slug result objects `{slug, response_json_path, dry_run_ok,
escalate?}` (a single-slug Task returns one such object); a one-slug
failure is recorded in that slug's object and does not abort the others,
and the editor iterates the array per-slug in CONSOLIDATE. This batching
does not relax the SEED / PERSON single-Task-per-round race rule. Use the harness-measured token
usage reported at the Task boundary (`subagent_tokens`), not the
subagent's self-reported `tokens_in/tokens_out` — children cannot
introspect their own tool-result intake and routinely undershoot it by
several fold.

**Two gather paths, two telemetry tiers.** Evidence reaches a slug's
ledger by either path; they land on different tiers, so read the tier
mix accordingly. `wikify work build-evidence` is a deterministic gather
(seed-doc chunks plus `corpus find --rank all` with structural
exclusions) with no per-chunk model call, so its cost lands on the
editor tier (M) — a round dominated by it shows ~zero haiku usage,
expected not a bug. The `gather-evidence` cluster skill instead fans out
cheap per-chunk haiku judges (tier H); dispatch it when you want model
judgment over chunks rather than a structural sweep.

Before dispatching the first Task of each wave, emit one
`pattern_dispatched` event per Task:

```
wikify run record-event --type pattern_dispatched \
  --stage explore --concept-id memristor --run <bundle> \
  --data '{"pattern": "P3", "target": "memristor", "depth": 0, "budget_chunks": <scaled>}'
```

`<scaled>` is the Sizing value `clamp(round(20 + 6 * log10(D)), 20, 60)`
(x1.5 for a central concept), NOT a flat 30 -- compute it before emitting.

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

`wikify work build-evidence` (the deterministic gather used by the
PERSON wave, dedup folds, and `extract-data`/`gather-evidence` commits)
does NOT self-emit `evidence_added`. The editor MUST emit one per slug
grown that way in CONSOLIDATE (e.g. `wikify work add evidence <slug>
--round N`), or `_growth_stalled` never sees the new evidence, the gate
holds the slug in `stalled`, and it never reaches `ready`.

Stages: `explore` for P1-P5 waves, `write` for the write wave, `data` for
the DATA wave (harvest + consolidate). DATA-wave Tasks bind to
`extract-data` and `consolidate-data`.

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

Every round MUST also record the full metric snapshot:

```bash
wikify run metrics --run <bundle> --round N --corpus <corpus>
```

It computes band_counts, chunk + addressable coverage, data counts,
committed pages, budget, and M1/M3, and appends one line to
`derived/stats.jsonl`. Run it every round so metrics are always computed
and reported, not only at finalize. `wikify run stats [--plot out.svg]`
retrieves and plots the coverage-and-pages-over-budget/iterations series
from that file.

### 8. STOP CHECK

Stop when ALL completeness signals hold:

- **Roster saturated.** `waves.roster_saturated` is true (the roster
  reached the SEED floor, `active_concepts >= target_min`) AND either no
  new `concept_suggestion` for 2 rounds (P5 emits only
  `evidence_suggestion`s) or `concept_count` flat for 2 rounds. A flat
  `concept_count` while `waves.seed_should_fire` is true is a STARVED
  roster, not a saturated one — do NOT stop; fire SEED. Likewise never
  stop while `waves.person_should_fire` is true.
- **Write queue drained.** No `ready` slug is unwritten.
- **Refine queue drained.** `work refine-candidates` returns empty
  (committed pages whose evidence outgrew their write-time snapshot have
  been refreshed).
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

Same close-out as baseline P5 plus chunk-coverage capture.

First, **drain the refine queue** so no committed page ships with evidence
that outgrew its write-time snapshot. Run `wikify work refine-candidates
--run <bundle> --format json`; for each candidate run the full `refine`
workflow (`wikify work claim <slug> --owner refine` -> `wikify draft build
<slug> --task refine` -> writer subagent -> `wikify draft finalize <slug>
--owner refine`), whose fresh `page_committed` resets that slug's baseline.
Repeat `refine-candidates` and drain again until it returns empty.

**Synthesize the literature gaps — do this BEFORE the rebuild/render
close-out** so the page lands in the rendered and evaluated site and
nothing mutates the run after `run close`. The GAP wave accumulated open
questions, contradictions, and understudied areas in
`work/notes/literature_gaps.md`, each line carrying a `chunk_id` anchor
and its verified `quote`. If the file has enough entries to field ~6
evidence markers:

1. `wikify work add concept "Literature Gaps and Open Questions" --kind
   article --run <bundle>`.
2. Commit the anchor chunks as its evidence, passing each note's exact
   quote (the `@-` JSON form carries the gap sentence; a bare comma-id
   list would store `text[:400]` instead, and `notebook-init --seed-docs`
   takes DOC handles, not chunk ids, so it is the wrong primitive):

   ```bash
   echo '[{"chunk_id":"<id>","score":1.0,"quote":"<exact gap quote>"}, ...]' \
     | wikify work build-evidence <slug> --from-ids @- \
         --corpus <corpus> --run <bundle>
   ```

3. `build-evidence` does not self-emit the growth event and the gate needs
   a fresh clearance, so record both before finalizing: `wikify work add
   evidence <slug> --round <N> --run <bundle>` (emits `evidence_added`),
   then `wikify run record-event --type page_recall_cleared --stage write
   --concept-id <slug> --run <bundle> --data '{"exhausted": true, "reason":
   "p5_gap_anchor_synthesis"}'`.
4. Write it through the normal write gate (writer subagent -> `draft check`
   -> `draft finalize <slug> --require-recall`), grouping the field's
   unresolved questions by theme, each claim carrying its `[^eN]` marker.
   Phrase claims as what the literature reports or has NOT established
   ("...remains debated", "no consensus on..."), never as corpus
   meta-commentary ("the corpus lacks...").

Skip the page when the notes file is empty or too thin for ~6 markers, and
put the gaps in the Final Report instead. Then run the close-out:

```bash
wikify work tend --run <bundle>
wikify data rebuild --run <bundle>   # refresh every committed data artifact
wikify wiki check --run <bundle>
wikify wiki rebuild --run <bundle>
wikify wiki navigation-context --run <bundle> \
  --out <bundle>/derived/navigation_context.json
# Invoke organize-wiki to write derived/navigation.json.
wikify render --bundle <bundle> --format html
wikify run close --status completed --run <bundle>
wikify eval --bundle <bundle> --corpus <corpus>
wikify work coverage --run <bundle> --corpus <corpus> --format json \
  > <bundle>/derived/coverage.json
wikify run stats --run <bundle> --plot <bundle>/derived/metrics.svg
```

`run stats --plot` produces the metrics chart from `derived/stats.jsonl`.

Then run the Inspection Loop and write the Final Report.

## Subagent contracts

The editor (this skill's main loop) runs at tier **L** (top-tier). All
roles below are subagents it dispatches; each may add an optional
`escalate` block to its return when a decision exceeds its mandate
(see Escalation).

| role | tier | skill | inputs | return |
|---|---|---|---|---|
| editor | **L** | this skill (main loop) | bundle, corpus | round events, dispatch plan, escalation rulings |
| explorer | sonnet M | `explore` | `pattern`, `target`, `run`, `corpus`, `budget_chunks`, `depth` | per-target envelope (see explorer skill) |
| classifier | haiku S | this skill (Re-entry) | `doc_id`, dossier index | `{overlapping_slugs: [...]}` |
| writer | sonnet M | `write-page` | `slug`(s), dossier path, evidence path | per-slug result `{slug, response_json_path, dry_run_ok, escalate?}`: one object for a single-slug Task, an ARRAY of them for a batched Task |
| refiner | sonnet M | `refine` | `slug`, dossier path, committed page | response.json path / committed |
| data-extractor | sonnet M | `extract-data` | `target` (docs or slug), `run`, `corpus` | `{submitted, stored, rejected}` |
| data-consolidator | sonnet M | `consolidate-data` | `run`, theme (properties) | committed artifact id |
| organizer | sonnet M | `organize-wiki` | navigation context | navigation.json |

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
`subskills/reference/references/writing/escalation.md`, which just re-runs at a
higher tier.)

## Dedup rulebook

The editor decides merges in CURATE over the candidate pairs
`cluster-concepts --by evidence` surfaces (plus any near-duplicate
titles), reading only titles and previews — never chunk bodies.

- **Merge** when evidence overlap is high (Jaccard >= 0.5) AND
  (semantic proximity OR subsumption OR lexical match). The canonical
  slug is the broader / more-cited concept; the narrower becomes an
  alias.
- **Keep distinct** when the pair shares sources but covers genuinely
  separate facets a reader would want apart. Sharing evidence is not
  sufficient; demand redundancy of the *concept*, not the sources.
- **When unsure, keep distinct** — a wrong merge is lossy and hard to
  undo; a missed merge is cheap to catch next CURATE.

The merge-execution commands (evidence fold, alias carry, tombstone
event) are in `references/dedup.md`. A `merged` / `parked` / `dropped`
card never re-enters `ready` / `growing`. The fold runs through
`build-evidence`, which does NOT self-emit `evidence_added` — emit one
for the canonical slug in CONSOLIDATE (see Hard Rules). **If either
page is already committed**, do NOT hand-edit — run `refine`.

## Sizing and defaults

Full formulas, the corpus-size knob table, the coverage-target ceilings,
the fixed per-Task knobs, and interruption handling are in
`references/sizing.md`. At setup read `D = health.n_docs` and
`Kc = health.n_chunks` and derive `wave_size`, `target_min`,
`max_rounds`, `expected_pages`, `expected_people`, and `budget_est`
from those formulas before round 0.

The knobs are **non-binding ceilings, not targets**: the loop stops
first on completeness (roster saturation + drained write queue +
coverage plateau; see STOP CHECK). The coverage signal to read is
`addressable_coverage_ratio` (target 0.33); `chunk_coverage_ratio`
(raw ceiling 0.25) cannot approach 1.0 by construction, so never set a
chunk-coverage stop target near 0.90. Editor tier is **L** (top-tier);
explorer/writer M, classifier S; claim owner `investigate`, TTL 1800 s.

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
  `refine` for that.
- **Cost curves are invalid without `type="call"` events.** Always
  record per Task.
- **Emit `evidence_added` for every slug grown via `build-evidence`.**
  That command (PERSON wave, dedup folds, ledger commits) does not
  self-emit the event the growth-stall gate keys off; without it the
  slug stays `stalled` and never reaches `ready`.

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

- `references/sizing.md`
- `references/dedup.md`
- `subskills/explore/SKILL.md`
- `subskills/search-corpus/SKILL.md`
- `subskills/search-wiki/SKILL.md`
- `subskills/bundle/SKILL.md`
- `subskills/gather-evidence/SKILL.md`
- `subskills/write-page/SKILL.md`
- `subskills/organize-wiki/SKILL.md`
- `subskills/reference/references/exploration/patterns.md`
- `subskills/reference/references/exploration/maturity.md`
- `subskills/reference/references/exploration/workflow-contracts.md`
- `subskills/reference/references/writing/escalation.md`
