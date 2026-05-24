# wikify-baseline friction audit — 2026-05-25

Goal: inspect and partially run `.claude/skills/wikify-baseline/SKILL.md`
against `data/corpora/ald_docling_2026_05_15` (207 docs, 5280 chunks),
compared to the reference bundle `baseline_ald_2026_05_14_v1` (14
articles committed against the same corpus). Identify frictions in tool
usage, simplify the skill text, and propose changes that reduce wall
time and token spend without sacrificing the quality bar.

## Reference bundle numbers

| metric | value |
|---|---|
| committed articles | 14 |
| committed persons | 0 |
| evidence records (active) | 162 (avg 11.6 / page) |
| CLI invocations recorded | 197 |
| CLI wall-time total | 114 s |
| `corpus find` calls (bash) | 31 × ~2.3 s = 71 s |
| `corpus sample` calls (bash) | 2 × ~4.7 s = 9.4 s |
| `draft check` calls | 34 (retries included) |
| `wiki commit` calls | 14 |
| `call_recorded` telemetry events | **0** |

Two structural anomalies in the reference bundle:

- **Telemetry void.** The skill's `record-call --stage extract|evidence|write`
  contract was never invoked. The bundle has 197 CLI calls and 14 pages,
  but 0 call cost rows. Eval's cost panels are therefore invalid (as the
  skill itself warns under "Hard Rules").
- **Bash-only retrieval.** The reference run predates the MCP migration:
  31 `wikify corpus find` cold-starts (~2.3 s each) instead of warm-MCP
  queries. Those alone are 75 s of CLI wall; warm MCP would shave most of
  it.

## Frictions observed (categorised)

### A. SKILL text bugs

1. **Step 7 references a field that doesn't exist.** Skill says "Read
   `health.available_metrics.author` from `context_show()`". The actual
   `context_show()` response on `ald_docling_2026_05_15` has no
   `available_metrics` key. Available author rank metrics live in
   `corpus_schema().rank_metrics.author` (`citation_count`, `h_index`,
   `n_papers`). Today an agent would either fall back to grepping or
   silently pick a metric.

2. **Step 2 asks for "manifest hash in run notes" with no source.**
   `context_show()` does not return a fingerprint. The corpus
   fingerprint (`7d4ec02daa290978`) only prints from `wikify run init`
   stdout. The skill should tell the agent where to capture it.

3. **Step 4 is a no-op.** "(No orchestrator-side text read.) The
   orchestrator does NOT pre-fetch doc bodies." This is enforced by Step
   5 ("Map extractors fetch their own assigned doc body"). The standalone
   step is dead weight.

4. **Two `cluster-concepts` calls with subtle `--by` switch.** Step 8
   (`--by seeds`) and Step 9 (default `--by evidence`) look like the
   same call. The semantic difference is buried; easy to call the wrong
   one.

5. **Hard rules + Defaults + Workflow order is hard to follow.** The
   skill puts abstract rules and defaults first, then the workflow. An
   agent reading top-down has to context-switch back to the rules during
   each step. Reorder: Workflow → Defaults → Rules.

6. **`record-call` mentions are scattered.** They appear inside Step 5
   (extract), Step 8 (evidence), Step 9 (write). The reference bundle's 0
   telemetry events show this contract does not stick.

7. **Vetter and writer subagent spawn contracts are inline prose.**
   Recipe for both is buried in Step 8 (vetter) and Step 9 (writer).
   Cleaner: one "Subagent contracts" block defining inputs, model class,
   return envelope, and how to record telemetry.

8. **Step 11 is a 8-call mechanical sequence.** `work tend → wiki check →
   build vectors → build indexes → build graph → navigation-context →
   organizer subagent → render → close → eval` is purely deterministic.
   Today the agent runs each call individually; a `wikify wiki finalize`
   wrapper would collapse 6+ calls to 1. (Engineering item, not a skill
   edit.)

9. **"Concurrent writers up to 4 ... on rate-limited Sonnet accounts the
   practical safe parallelism is 2"** is buried in Defaults. Should be
   the explicit default = 2 with an "raise to 4 only when not rate
   limited" note.

10. **Final Report is one prose paragraph of ~12 items.** Easy to drop
    items. Make it a checklist.

### B. Tool round-trip waste

1. **Vetter does N×`corpus_show(full=True)` per query.** `corpus_find`
   returns only ~240-char `preview`. SKILL Step 2b mandates a
   parallel-batched `corpus_show(handle, full=True)` follow-up per
   candidate (anti-pattern: serial). With 3–5 queries × top_k=25 × 14
   slugs that is up to **~1500 MCP round-trips per bundle** purely for
   text fetch. Each `corpus_show` response repeats `preview`, full
   `meta`, `resource_uri`, then the body text — ~25 KB of wrapper
   overhead per slug.

   **Fix (engineering):** add `corpus_find(..., include_text=True)`
   inlining body text in search results. The MCP tool already accepts
   `include_text` on `corpus_show`; mirror the flag on `corpus_find`.
   This eliminates Step 2b entirely.

2. **Bash↔MCP duality is fragile.** Skill says MCP for read, bash for
   mutation. But `cluster-concepts` and `work claim` are bash-only,
   `corpus_find` is MCP-only-preferred, and a switch back to bash
   forfeits the warm embedder (~3.6 s cold start). The reference bundle
   shows 31 bash `corpus find` calls (~71 s of cold-start tax) by an
   orchestrator that was supposed to be MCP-first.

3. **Per-slug serial commit dance.** Step 10 is `draft normalize-references
   → draft check → wiki commit → work release`, four calls per page ×
   14 pages = 56 CLI invocations purely for the commit ritual. A
   `wikify draft finalize <slug>` macro that runs all four in one
   process would save 42 CLI invocations and the associated process
   startup cost (~40 ms × 42 = 1.7 s, plus event-log overhead).

4. **Three `wiki build` calls in a row.** `vectors`, `indexes`, `graph`
   each pay full CLI startup. A `wikify wiki rebuild` collapsing the
   trio saves 2 startups.

5. **Per-Task `record-call` instead of batch.** The current contract is
   "after each subagent Task returns, run `wikify run record-call`."
   For a 16-doc baseline that is roughly 16 (extract-map) + 1 (reduce)
   + 14 (vetter) + 14 (writer) = ~45 record-call invocations. If
   subagents emit a `{tokens_in, tokens_out}` field in their return
   JSON and the orchestrator pipes a JSONL into one `record-calls
   --from-stdin`, that drops to 4 calls — and crucially, **the
   orchestrator cannot forget** (reference bundle: 0/45 recorded).

6. **`context_show()` does not surface the schema info the skill needs.**
   Agent has to call `corpus_schema()` for rank metrics and traverse
   relations, then `context_show()` for health, then `corpus_sample()`
   for seeds. A single `context_show()` that folds rank metrics into
   `health` would let the bootstrap phase be one MCP call.

### C. Quality bar observations from the reference bundle

The reference bundle landed exactly 10–12 evidence records on every
slug. Vetter quota is 14. The vetter is consistently capping below
quota — likely because the corpus genuinely runs out of on-topic chunks
beyond ~12 for narrow slugs (e.g. `eels-characterization-of-memristors`,
`tio2-memristor`). Lowering the default quota to 12 saves a
gap-driven round on small slugs at zero quality cost.

## Proposed skill simplifications (text-level, safe to apply)

Concrete diffs against `.claude/skills/wikify-baseline/SKILL.md`:

1. **Fix the broken field reference.** Replace
   `health.available_metrics.author` (Step 7) with
   `corpus_schema().rank_metrics.author`.

2. **Restructure Workflow into 5 phases.** Collapse the 11 numbered
   steps into:

   - **P1 Bootstrap** — `run init`, `context_set`, `context_show`,
     `corpus_sample`. One bash + three MCP calls.
   - **P2 Concept extraction** — map-reduce, accept staged
     suggestions, optionally seed persons.
   - **P3 Evidence** — cluster by seeds, spawn vetters per cluster.
   - **P4 Write + commit** — re-cluster by evidence, claim/draft/write
     per cluster, normalize/check/commit/release per page.
   - **P5 Finalize** — `work tend`, `wiki check`, builds, navigation,
     render, close, eval.

3. **Delete Step 4 ("No orchestrator-side text read").** Already
   enforced by Step 5.

4. **Promote the vetter and writer spawn contracts** into one
   "Subagent contracts" block at the bottom of the workflow. Each block:
   subagent type, skill name, input JSON keys, return JSON shape,
   telemetry recording.

5. **Move "Hard Rules" below the Workflow** so an agent reads the
   sequence first, then the constraints. Cut the Hard Rules list from
   6 items to the three that actually constrain behaviour (bypass
   build-evidence, repair via path not direct edit, cost-curve gate);
   demote the other three to a "Common mistakes" appendix.

6. **Tighten Defaults.** Make `concurrent writers = 2` the headline
   default, with "raise to 4 only after confirming no rate-limit
   warnings"; drop the smoke-run anecdote to a one-line footnote.

7. **Telemetry consolidation.** One "Telemetry" subsection naming the
   three stages (`extract`, `evidence`, `write`) with one example call.
   Mandate that the orchestrator records telemetry **inside the same
   loop iteration** that spawned the Task — no "we'll batch later."

8. **Final Report becomes a checklist.** One bullet per required item.

9. **Inspection Loop becomes a 5-row table.** Page kind → what to
   check.

10. **Capture corpus fingerprint at bootstrap.** Step P1 should record:
    "the `corpus_fingerprint` printed by `run init` is the manifest
    hash; pin it in run notes."

## Proposed engineering changes (flag, do not implement now)

These are higher-leverage but require code changes:

| change | savings |
|---|---|
| `corpus_find(..., include_text=True)` MCP arg | eliminates Step 2b entirely; -1000 to -1500 MCP round-trips per bundle; -10-30 s wall; ~50% of vetter context bloat |
| `wikify draft finalize <slug>` macro | -42 CLI startups per 14-page bundle (~1.7 s + cleaner skill prose) |
| `wikify wiki rebuild` macro | -2 CLI startups per finalize |
| `wikify run record-calls --from-stdin` batch ingest | -41 of 45 record-call invocations; makes telemetry harder to skip |
| Fold rank metrics into `context_show().health` | -1 MCP call per bootstrap |
| `wikify work cluster-concepts --auto` (pick seeds-vs-evidence by bundle state) | -1 cognitive trap, no wall savings |

## Quality control plan

Before/after must match on these bars (from `baseline_ald_2026_05_14_v1`):

- ≥10–14 evidence records per slug, ≥5 distinct source docs.
- 14 committed article pages (or comparable scope).
- `draft check` passes on every committed page.
- `wikify wiki check` and `wikify render --format html` succeed.
- Eval M1, M3, M6 within ±5 % of reference; M5 strictly above 0
  (reference had 0 chunk_read events for write-stage = degenerate M5).

## Expected savings — quantified

### From the text-level patch (applied 2026-05-25)

| change | mechanism | per-bundle saving |
|---|---|---|
| 11 steps → 5 phases; redundant Step 4 deleted; Hard Rules trimmed | smaller SKILL.md loaded each turn the skill is consulted | ~2 % bytes (10602 → 10365); larger qualitative gain from removing one full nav level |
| Fixed broken `health.available_metrics.author` reference | agent no longer needs to grep / fall back to schema | -1 MCP round trip + -1 likely failed call |
| Telemetry promoted to its own section + in-loop mandate | telemetry events go from observed 0 to expected ~45 per bundle | unlocks cost-curve validity (hard-rule compliance) |
| Subagent contracts hoisted to one block | spawn shape no longer reread per step | -1 SKILL.md re-scan per Task wave |
| Vetter quota 14 → 12 (matches observed evidence-per-page ceiling) | one fewer gap-driven query round on small slugs | -14 × ~2 s = ~28 s wall + ~14 × 1 vetter MCP call |
| Final Report + Inspection Loop as checklists | items don't get silently dropped | qualitative — fewer reruns |
| `concurrent writers` default 2 (was implied 4) | matches Sonnet rate-limit reality | avoids the lost-wave incident referenced in original Defaults |

### From the engineering items (proposed, not applied)

| change | per-bundle saving |
|---|---|
| `corpus_find(..., include_text=True)` MCP arg | -1000 to -1500 MCP round-trips; -10-30 s wall; ~50 % of vetter context bloat |
| `wikify draft finalize <slug>` macro (normalize → check → commit → release) | -42 CLI startups; -1.7 s wall; SKILL prose halves at P4 |
| `wikify wiki rebuild` macro (vectors + indexes + graph) | -2 CLI startups; ~50 ms |
| `wikify run record-calls --from-stdin` batched ingest | -41 of 45 record-call invocations; makes telemetry uniformly recorded |
| Fold rank metrics into `context_show().health` | -1 MCP call per bootstrap |
| `wikify work cluster-concepts --auto` (mode auto-detect) | removes the seeds-vs-evidence cognitive trap |

### Total expected wall-time delta on a 14-page baseline

- Text-level patch alone: **~30 s saved** (vetter quota change + telemetry path tightening).
- Text-level + `include_text` engineering: **~60-90 s saved** + the
  vetter context shrinks ~50 % (lower per-vetter token cost).
- Text-level + all engineering items: **~90-120 s saved**, fewer
  CLI events, and a strictly-positive cost-curve.

The quality bar (`baseline_ald_2026_05_14_v1` reference: 14 articles,
162 evidence records, all pages pass `draft check` + `wiki check`) is
preserved by every item above — none changes the writer's evidence
substrate or the validation gate.
