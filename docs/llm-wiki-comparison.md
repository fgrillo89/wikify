# Comparison: `nashsu/llm_wiki` vs wikify

A study of [llm_wiki](https://github.com/nashsu/llm_wiki) (3.2k-star Tauri
desktop app) against the post-pivot wikify CLI, with concrete pattern
proposals scoped against the skill-pivot's published invariants.

This doc records **learnings only** — implementation is deferred.

## Correction up front

A first pass of this analysis claimed wikify lacked source-level
ingest caching. That was wrong. We already have it:

- `src/wikify/ingest/pipeline.py::content_hash()` (line 169) — sha1[:12]
  of file bytes; the same hash anchors `doc_id`.
- `_prepare_change_set()` (line 940-972) — manifest-backed skip logic
  with intra-run (`[skip-intra]`) and cross-run (`[skip-cross]`)
  deduplication keyed on `content_hash`.
- `manifest.py::SourceRecord.content_hash` field persists across runs.
- `_recover_completed()` (line 431-481) reconstructs receipts when a
  prior ingest crashed mid-run.

**Adoption verdict:** the llm_wiki ingest-cache pattern is already in
place. No work needed. A possible refinement (sidecar hash-cache to
avoid re-reading source bytes on every ingest) is a small optimisation,
not a structural addition.

## What llm_wiki is

A Tauri desktop app where the wiki is a directory of markdown files
with YAML frontmatter. Ingest is a single function `autoIngest()` in
`src/lib/ingest.ts` (~1000 LOC) that runs a two-stage LLM pipeline:

1. **Analysis prompt**: reads the existing wiki + new source, produces
   structured analysis identifying entities, concepts, contradictions,
   recommendations.
2. **Generation prompt**: emits `---FILE: wiki/.../foo.md ---` blocks
   parsed by a state machine into actual page files.

Persistence is markdown + YAML; there is no SQLite, no Pydantic
models, no session.json. Dedup is implicit — the LLM reads
`wiki/index.md` + `wiki/overview.md` in context. Cross-references are
`[[wikilinks]]`. Project lock + per-source content-hash cache prevent
races and reprocessing.

## Where the design philosophies differ

| Axis | llm_wiki | wikify |
|---|---|---|
| Persistence | Markdown + YAML only | Markdown pages + JSON session/scratch/run |
| Dedup | LLM-implicit, in-context | Explicit `distill.dossier.canonicalize` |
| LLM calls per ingest | 2 (analysis + generation) | N×extract + N×write + N×validate, tier-gated |
| Citation grounding | `sources: []` frontmatter array | `[^eN]:` markers + verbatim-substring chunk grounding |
| State | Implicit in markdown + frontmatter | Explicit `session.json` with status machine |
| Schema | Static `schema.md` per project, prose | Pydantic models + versioned envelopes |
| Validation | Optional REVIEW blocks | Hard `wikify validate write` gate |
| CLI | None (desktop app) | 14 commands across 7 deterministic + 7 skill-driven |
| Tests | TS test files | 702 pytest tests |

The two designs are **not in competition** — they target different
problems. llm_wiki optimises for single-user desktop UX with the
simplest possible state. wikify optimises for budget-controlled,
parity-testable, multi-strategy comparison. Neither is strictly
simpler; they're optimal for different requirements.

## Patterns considered, by alignment with the pivot

The skill-centric pivot commits to specific invariants in
`docs/skill-centric-pivot.md`:

- Python does not call models.
- Skills own the per-iteration loop.
- Files are the agent-backend interface.
- No hidden state.
- No validation layer bypass — canonical artifacts are validated.
- Enumerable file types; the agent does not invent new types mid-workflow.

Each pattern below was checked against these invariants.

### Rejected: source-tracking frontmatter (`sources: [doc_id, ...]` on pages)

**Status: redundant.**

Citation grounding already tracks sources at chunk granularity via the
`[^eN]: <chunk_id> (<doc_id>) > "<quote>"` reference format documented
in `reference/citation-format.md`. Each chunk_id contains its doc_id
prefix. The `_wiki_graph.json` already encodes citation edges between
pages and source documents.

Adding a `sources` frontmatter field would duplicate this without
adding information. Worse, it would diverge from canonical citation
edges over time as pages are edited.

### Rejected: implicit-merge writer pass (LLM asks "does this duplicate any existing page?")

**Status: violates the no-hidden-state invariant.**

llm_wiki achieves this by feeding `index.md` + `overview.md` into the
writer prompt and trusting the LLM. That works for them because they
have no separate canonicalisation step. We do — `wikify extract
canonicalize`.

If we want the same behaviour against fuzzy title variants, the
correct shape is a deterministic CLI:

```
wikify validate dedup \
    --session <path> \
    --candidate-page-id "<title>" \
    [--threshold 0.85]
```

Output: structured verdict with `{is_duplicate: bool, of: page_id|null,
score: float, evidence: ...}`. The skill calls it before draft. Result
is testable, reproducible, schema-versioned.

This is **deferred** — not part of any current proposal — but flagged
as the right shape if the user later observes deterministic
canonicalize missing fuzzy duplicates in real corpora.

### Reframed: REVIEW blocks for soft signals (separate scratch artifact)

**Original proposal (rejected):** add `review_items: list[str]` to
`WriteResponse` Pydantic model. Writer subagent emits non-blocking
yellow flags ("might duplicate X", "missing entity Y") inline.

**Why rejected:** mutates the canonical response schema, blurs the
validation gate, and embeds soft signals in a model that's
`extra="forbid"` and parity-tested against legacy.

**Reframed feature spec:**

A separate scratch artifact, owned by its own CLI surface. The
canonical `WriteResponse` stays untouched.

```
<bundle>/_scratch/review-<page_id>.json
```

Schema:

```json
{
  "schema_version": 1,
  "page_id": "<id>",
  "response_path": "<path>",
  "items": [
    {
      "kind": "potential_duplicate" | "missing_entity" | "contradiction" | "stale" | "thin_evidence",
      "severity": "info" | "warning",
      "message": "<short prose>",
      "evidence": [{"page_id": "...", "chunk_id": "..."}]
    }
  ],
  "emitted_at": "..."
}
```

CLI:

- `wikify reviews record --session <p> --response <p> --items <json>`
  appends a review file (skill-emitted from a tier-S subagent reflection
  pass, called separately from the write subagent).
- `wikify reviews list --session <p>` shows pending reviews.
- `wikify reviews dismiss --session <p> --page-id <id>` clears them
  after human triage.

Why this works under the pivot's rules:

- Doesn't mutate the canonical `WriteResponse` schema.
- Is a separate enumerated file type (would be added to
  `reference/schemas.md` allowed-file list).
- Validation gate semantics unchanged — reviews are advisory, not gating.
- All emission goes through a CLI, so the agent never invents file types.

Estimated cost: ~150 LOC + tests. No change to existing skill workflow
required; it's an opt-in surface for future strategies that want
human-in-the-loop signals.

### Deferred: `<bundle>/wiki/log.md` human-readable audit log

**Status: nice-to-have, not blocking.**

llm_wiki appends `## [YYYY-MM-DD] ingest | Title` lines to `wiki/log.md`
on every ingest. We have `_run_history.jsonl` (per-close snapshots) and
`_calls.jsonl` (per-model-call records) but nothing human-grepped.

A `wiki/log.md` would be a thin convenience layer:

- `wikify bundle commit-page` appends a line.
- `wikify session close` appends a final line.
- File is purely informational; no programmatic consumer.

Defer until Phase 4+ when scripted/guided strategies start producing
multi-iteration runs whose `_run_history.jsonl` density makes a
human-readable narrative valuable.

### Deferred: project archetype templates (`wikify session init --template research|reading|business`)

**Status: out of scope until strategy parameter sets crystallise.**

llm_wiki ships 5 hardcoded templates. Each pre-seeds a `purpose.md` +
`schema.md` + frontmatter conventions for a specific domain (research
papers, books, business decisions, etc.).

The wikify equivalent would seed `BaselineConfig` defaults plus a
`<bundle>/purpose.md` document. This is genuinely user-facing UX, but
it's premature: scripted-E/M/X and guided strategies are Phase 4+, and
their parameter sets aren't stable yet. Adding templates now would
ship an opinion before we know what the right opinions are.

### Already done: source-hash ingest cache

See "Correction up front" above. `manifest.py::SourceRecord.content_hash`
+ `_prepare_change_set` already implement this.

A possible refinement: a sidecar hash-cache file (`corpus/.hash-cache`)
that stores `path → hash` pairs so subsequent ingests don't re-read
file bytes to recompute the hash. Small optimisation, not a structural
change.

## Summary

| Pattern | Adopt? | Form |
|---|---|---|
| Source-tracking frontmatter on pages | ❌ Redundant | Already covered by `[^eN]:` chunk citations |
| Implicit-merge writer pass | ❌ Violates determinism invariant | If needed: explicit `wikify validate dedup` CLI (deferred) |
| REVIEW blocks (original embedded form) | ❌ Violates schema invariant | Reframe as separate scratch artifact + `wikify reviews` CLI family (deferred) |
| `wiki/log.md` audit log | ⏸ Defer | Phase 4+, once multi-iteration runs make it valuable |
| Project archetype templates | ⏸ Defer | Phase 4+, once strategy parameter sets stabilise |
| Source-hash ingest cache | ✅ Already done | `manifest.py::SourceRecord.content_hash` |

**Net new feature specs from this study (deferred, not yet scheduled):**

1. `wikify validate dedup` CLI for deterministic fuzzy-duplicate detection.
2. `wikify reviews` CLI family + `<bundle>/_scratch/review-<page_id>.json` artifact for non-blocking writer reflections.
3. `<bundle>/wiki/log.md` human-readable audit log appended on commit + close.
4. `wikify session init --template <archetype>` for project scaffolding.
5. (Optional micro-optimisation) Sidecar hash-cache for the existing manifest dedup, to skip re-reading source bytes on every ingest.

None of these block the current pivot phases (A through D). All are
additive surfaces compatible with the published architecture.

## Appendix: `alexzhang13/rlm` — different layer, brief note

[rlm](https://github.com/alexzhang13/rlm) (3.8k-star Python library) is a
plug-and-play **inference primitive** for "Recursive Language Models":
replace `model.completion(prompt)` with `rlm.completion(prompt)` and the
LM runs inside a sandboxed Python REPL where it has globals
`context` (the input as a Python variable), `llm_query(prompt)` (plain
single-shot call), and `rlm_query(prompt)` (recursive child RLM call,
falls back to `llm_query` at `max_depth`). Sandboxes range from
in-process `exec()` to Modal/Daytona/Docker/E2B. Depth and budget
cascade to children: `child = RLM(..., max_timeout=remaining_timeout,
max_budget=remaining_budget)`.

This is a different layer than wikify. rlm sits at the **LM API layer**
(one function call, recursive, sandbox-isolated). wikify sits at the
**workflow layer** (skill prose orchestrates per-step CLI invocations
and Task subagents). Both share the pattern "the LM programmatically
controls context decomposition," but at different abstraction levels.
There is no clean "adopt rlm" play; the substrates are different
(sandboxed Python REPL vs. Claude Code Task tool + filesystem) and
adopting rlm would re-introduce LM-controlled mutable execution that
the pivot deliberately removed.

**One pattern worth carrying over: budget propagation to subagents.**
Today wikify tracks `haiku_eq_spent` vs `haiku_eq_target` on the
session but does not surface "remaining" to a subagent's prompt. A
small refinement (~30 LOC, deferred) is to extend `WriteRequest` with
a `budget_remaining_haiku_eq` field that `wikify draft write-request`
populates from `session.budget`, so the writer subagent can self-throttle
near the ceiling. Mirrors rlm's `remaining_budget = parent − spent` cascade.

Two more rlm patterns are notes for future strategies, not current
proposals: **depth-bounded recursion** (relevant only if a future
guided strategy decomposes hard pages into sub-pages), and rlm's
**multi-client abstraction** in `rlm/clients/` (relevant only if
wikify ever leaves Claude Code as the runtime, in which case the
`clients/` shape is a reference for the vendor-portable layer the
pivot doc claims at its core).

Not worth borrowing: the REPL-as-execution-substrate, in-process
`exec()`, or rlm itself as a dependency.
