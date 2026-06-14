# wikify-investigate friction inventory (remote-control run 2026-06-14)

Bundle `data/bundles/ald_2026_05_15_wiki`, corpus `ald_docling_2026_05_15`
(207 docs / 5280 chunks). Editor Opus, explorers/writers sonnet.
4 rounds run; 3 pages committed. Issues hit, by severity.

## Blocker

### F1. Evidence `chunk_id` stored as handle, not canonical corpus id
- **Symptom**: after round 1, `work coverage` reported 0.0 despite 52
  evidence records on disk. Maturity (which only counts records) looked
  fine, masking it.
- **Cause**: explorers stored the short `chunk:<hex>` handle. Coverage
  (`coverage.py:_in_flight_chunk_ids`) intersects against the corpus
  `chunks.chunk_id` canonical long form (`<title>_<dochex>__cNNNN_<hex>`).
  Handles never match -> empty intersection. Citation grounding at
  commit would hit the same wall.
- **Fix**:
  1. `wikify work add evidence` should resolve+validate `chunk_id`
     against the bound corpus (handle -> canonical, reject unknown ids)
     so bad ids cannot enter a ledger.
  2. `wikify-investigate-explore` SKILL must state: store the canonical
     `id` field from corpus_find/corpus_show, never `chunk_handle`.
  3. Defensive: make `_in_flight_chunk_ids` / committed-chunk lookup
     normalize both sides (suffix-hex tolerant) so coverage is robust.
- **Workaround applied**: remapped all ledgers + inbox via suffix-hex
  match against the corpus DB (52 + 20 records, 0 misses).

## High

### F2. `wikify run record-event` ignores stdin; SKILL example pipes to it
- **Symptom**: every `round_started`/`evidence_added`/`pattern_dispatched`
  landed with `round: None`.
- **Cause**: the command reads `--data`; the SKILL shows
  `echo '{...}' | wikify run record-event ...`. The piped JSON is
  silently discarded.
- **Downstream**: `round=None` -> `_growth_stalled` baseline fallback
  (True) -> every slug "stalled" -> GROW wave (targets "growing") finds
  nothing -> loop starves. Cost real diagnosis time.
- **Fix**: (a) correct the SKILL example to `--data '{...}'` (attempted;
  edit was permission-denied as skill self-modification — left for the
  user). (b) `record-event` should accept stdin when `--data` absent,
  or hard-error if stdin is non-empty and ignored. (c) validate
  required payload per event type (e.g. `round_started.round` is int).

### F3. `evidence_added` / `round_started` are undocumented hard
dependencies the editor must emit by hand
- **Symptom**: maturity bands wrong until I emitted these manually.
- **Cause**: `work add evidence` does not emit `evidence_added`;
  nothing emits `round_started`. Maturity silently couples to both.
- **Fix**: `work add evidence` should emit `evidence_added` (pass
  `--round`), or `work tend` should reconcile evidence deltas into
  events. At minimum the investigate SKILL must list the per-round
  event contract explicitly.

### F4. `growth_stalled` is a readiness GATE -> write lags growth by 2
rounds, undocumented
- **Symptom**: a slug with 32 chunks / 26 docs / all kinds scored 0.0
  ("growing") the round it was grown; only became "ready" (0.99) two
  rounds later.
- **Cause**: `growth_stalled` (no `evidence_added` in last 2 rounds) is
  one of the AND-ed hard gates. Correct by design (write when
  saturated) but nowhere does the loop doc say "grow, then leave a slug
  untouched 2 rounds before it can be written."
- **Fix**: document the grow -> cool-down(2) -> ready -> write rhythm in
  the investigate SKILL. Consider an early-exit: allow write when
  gates+score are strong AND the last round added < k new chunks
  (saturation by delta, not by a fixed 2-round timer), so a
  one-shot-strong slug is not forced to idle.

### F5. P5 inbox `evidence_suggestion` records also carry short handles
- Same root as F1, on the suggestion path. `work tend` would fold
  handles into ledgers. Fix with the F1.1 normalization at the
  `add evidence` / `tend` boundary so every write path is covered.

## Medium

### F6. Telemetry undercounts; editor records envelope self-reports
- Subagent self-reported `tokens_in/out` undershoot real usage 3-10x
  (already in lessons.md). The Agent tool result DOES expose harness
  `subagent_tokens` at the boundary -- the editor should record THAT in
  `run record-calls`, not the child's self-estimate. Recorded budget
  (4.7M haiku-eq) is well under true consumption.

### F7. SEED slug-race vs "slug-disjoint by construction"
- Precedence says "one P1 Task per top-K PageRank doc," but P1 tasks
  CREATE slugs, and two docs can yield the same concept -> two parallel
  tasks racing on one slug folder. Contradicts the slug-disjoint hard
  rule (which assumes pre-existing slugs).
- **Fix**: state that SEED runs as ONE task over the doc list (internal
  dedup), or that concurrent SEED tasks must post-merge via `tend`.
  Workaround applied: single multi-doc P1 task per round.

### F8. No query-less PageRank doc ranking
- `corpus find --rank pagerank` with a query returns semantic scores,
  not the metric-only `pr=` view; but a query is required, so getting a
  pure top-PageRank doc list is awkward.
- **Fix**: allow `corpus find --rank pagerank` query-less, or point the
  search-corpus SKILL at the right incantation.

## Low / cleanup

- **F9**. `wikify work list` does not expose evidence count; had to call
  `work maturity` to see per-slug `n_chunks`. Add a column.
- **F10**. Explorers leave `work/evidence_staging/<slug>.jsonl`
  (short-handle duplicates of committed evidence). Clutter that could
  mislead re-entry. `tend` should sweep staging after commit.
- **F11**. `draft build`/`finalize` require explicit
  `--model-id/--tier/--owner` every call -- verbose for a loop;
  could default from run state.
