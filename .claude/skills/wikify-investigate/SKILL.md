---
name: wikify-investigate
description: Researcher-style iterative wiki builder. Editor orchestrator dispatches explorer subagents that walk the corpus via named recursive patterns (P1-P5), gather evidence into notebook dossiers, and write pages when a composite maturity score crosses the gate. Coverage of the corpus chunk set is the primary objective. Re-entrant on the same bundle when new corpus material arrives.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-investigate

Editor orchestrator that builds a wiki the way a researcher would:
read top papers, anchor concepts, hop chunks, walk citations, sweep
exact terms, gather evidence into per-slug notebooks, and write only
once a composite maturity score passes a gate. Pushed to its limit,
the loop's gap-explorer pattern (P5) drives `chunk_coverage_ratio`
toward 1.0.

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

```bash
wikify run init --bundle <bundle> --corpus <corpus> \
  --strategy investigate --target-haiku-eq 30000000
```

Bind MCP:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
```

Record the `corpus_fingerprint` from `context_show().health.fingerprint`
into run notes; it gates the re-entry path.

### 1. SENSE

Read in this exact order:

- `wikify run show --full --run <bundle>` for budget + last round.
- `wikify work list --run <bundle> --format json` for slug roster.
- `wikify work maturity --all --run <bundle> --format json --round <N>`
  for the per-slug score + band.
- `wikify work coverage --run <bundle> --corpus <corpus> --format json`
  for `chunk_coverage_ratio`.
- If `derived/eval.json` exists, read `M3.g_evidence.modularity` for the
  bridge rule; otherwise treat modularity as `null` (bridge does not fire
  in round 0).

Group slugs by `band`: `ready`, `growing`, `stalled`, `new`, `parked`.

If `corpus_fingerprint` differs from the value last written to
`state.json`, emit `corpus_drift_detected` and force a SEED wave next
round (see Re-entry).

### 2. DECIDE — fixed precedence

Build a dispatch plan that is **slug-disjoint by construction**: at
most one Task per slug per round. Walk the precedence list, attaching
targets to the plan in order, removing them from later bands.

1. **WRITE wave.** Every slug in `ready` band. Up to `wave_size = 2`
   per round. Eager — writing is terminal.
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
4. **SEED wave.** Fires when `concept_count < target_min` (default
   `target_min = max(10, ceil(0.6 * baseline_target))`) OR every dossier
   is `ready`/`stalled`. One **P1** Task per top-K uncovered
   PageRank doc, where K is `max(target_min - concept_count, wave_size)`.
5. **GAP wave.** Fires every round, low cost. One **P5** Task on the
   top 20 uncovered chunks by PageRank.

**Anti-starvation slack.** If the loop would otherwise stop (`STOP
CHECK` would fire) AND SEED or GAP would still produce work, dispatch
one half-size SEED+GAP wave before terminating.

### 3. DISPATCH

For each plan entry, spawn one `Task` (sonnet tier) bound to
`wikify-investigate-explore` for explore Tasks or `wikify-write-page`
for the write wave. Pass `pattern`, `target`, `budget_chunks`, `depth`
verbatim from the plan. Record `{role, model_id, tier, tokens_in,
tokens_out, stage}` from each return.

Before dispatching the first Task of each wave, emit one
`pattern_dispatched` event per Task:

```
echo '{"pattern": "P3", "target": "memristor", "depth": 0,
       "budget_chunks": 30}' \
  | wikify run record-event --type pattern_dispatched \
      --stage explore --concept-id memristor --run <bundle>
```

Stages: `explore` for P1-P5 waves, `write` for the write wave.

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
suggestions.

### 5. REASSESS

Recompute maturity for *touched slugs only* (slugs whose
`evidence.jsonl` or notebook changed this round). Cheap: one CLI call
per slug or a single `--all` call.

Recompute M1 only on rounds where at least one page committed (M1
cannot move otherwise; saves the chunk-embedding pass). Coverage is
cheap; recompute every round.

### 6. CURATE (every `curate_every = 2` rounds)

No subagents. In-editor:

1. Read `work/inbox/concept_suggestions.jsonl` if any survived tend.
   Dedup by canonical title; emit `merge_suggestion` records for
   near-duplicate variants (Levenshtein distance over normalised
   titles).
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

Stop if ANY:

- `budget_haiku_eq >= target`
- `rounds >= max_rounds` (hard ceiling 8)
- `chunk_coverage_ratio >= coverage_target` (default 0.85)
- `delta_coverage_per_round < 0.01` for 2 consecutive rounds AND no
  new dossier crossed the promotion threshold in those rounds
- No candidate action in any precedence band fires after the
  anti-starvation slack

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
   - Update `state.json.corpus_fingerprint` (`wikify run set` if
     available; otherwise emit a `corpus_drift_detected` event with
     the new fingerprint and rely on it as state).

## Finalize (after STOP)

Same close-out as baseline P5 plus chunk-coverage capture:

```bash
wikify work tend --run <bundle>
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

| role | tier | skill | inputs | return |
|---|---|---|---|---|
| explorer | sonnet M | `wikify-investigate-explore` | `pattern`, `target`, `run`, `corpus`, `budget_chunks`, `depth` | per-target envelope (see explorer skill) |
| classifier | haiku S | this skill (Re-entry) | `doc_id`, dossier index | `{overlapping_slugs: [...]}` |
| writer | sonnet M | `wikify-write-page` | `slug`, dossier path, evidence path | response.json path |
| organizer | sonnet M | `wikify-organize-wiki` | navigation context | navigation.json |

Every Task return must yield `{tokens_in, tokens_out, model_id}` for
the Telemetry pass below.

## Defaults

- `max_rounds = 8`, `wave_size = 2`, `curate_every = 2`.
- `target_min = max(10, ceil(0.6 * 16))` = 10 concepts.
- `coverage_target = 0.85`.
- Vetter / explorer budget per Task: `budget_chunks = 30`,
  `depth = 2` for P1, `depth = 1` for P2.
- Writer tier: M. Explorer tier: M. Classifier tier: S.
- Concurrent explorers: **default 2**, raise to 4 only if rate limits hold.
- Claim owner: `investigate`. Claim TTL: 1800 seconds.
- Budget: ~30M haiku-eq for a fresh 12-16-doc corpus. Investigate
  spends more than baseline by design — extra cost buys deeper
  evidence + coverage.

## Hard Rules

- **One Task per slug per round.** The dispatch plan is slug-disjoint.
  Never spawn two waves that share a slug.
- **The editor never reads chunk text.** It reads slug-level
  summaries, scores, and event envelopes. Explorers do all chunk
  reading.
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

## Stop Conditions

(Mirrors STOP CHECK above; restated here for the final report.) Budget,
max_rounds, coverage_target, plateau, or no candidate action.

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
