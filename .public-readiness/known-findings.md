# Known findings — triage for Docs / Skill-rewrite / Packaging phases

Six addressable issues from the prior investigate session, each verified
against the current `wikify-public-readiness-prep` tree and triaged to
one of: **fix-in-code** | **document** | **defer-as-issue**.

Quick-action summary:

| # | finding | decision | quick safe code fix now? |
|---|---------|----------|--------------------------|
| F1 | `build-evidence` emits no `evidence_added` event | fix-in-code (+ skill note) | **YES** |
| F2 | `draft finalize` consumes draft.json; 2nd call `draft_not_found` | document call-once (+ defer idempotency) | no |
| F3 | `data add` does not resolve the `chunk:` handle | fix-in-code (+ skill note) | **YES** |
| F4 | `kind=data` pages are a store separate from the wiki graph | document (docs + query skill) | no (by design) |
| F5 | `chunk_coverage_ratio` ~0.90 is structurally impossible | document (code already says so) | no |
| F6 | two gather paths route telemetry to different tiers | document (skill + docs) | no |

---

## F1. `build-evidence` does not emit `evidence_added` — maturity stall timer never advances

**Verified.** `cmd_build_evidence` (`src/wikify/cli/work.py:648`) appends
records via `append_evidence` (`work.py:976`, `work.py:1119`) but never
calls `append_event(type="evidence_added", ...)`. Only `cmd_add_evidence`
(the `work add evidence` command, `work.py:275`) emits that event, and it
*already* carries a `--round` option (`work.py:279-282`) that writes
`data["round"]` (`work.py:371-388`).
`_growth_stalled` (`src/wikify/bundle/work/maturity.py:155`) keys off
`evidence_added` events scoped to the latest `round_started` round:
a slug grown only through `build-evidence` therefore looks permanently
stalled (`maturity.py:172-179`), forcing the editor into the `stalled`
band (`maturity.py:440-441`).

**Decision: fix-in-code (quick, safe, do now).**
Add a `--round` option to `build-evidence` and, after the final
`append_evidence`, emit an `evidence_added` event mirroring
`cmd_add_evidence` (`work.py:371-388`): load state, build
`{"n": n, "round": round_num}`, `append_event(... type="evidence_added",
concept_id=concept ...)`. Self-emission removes the hidden editor
responsibility entirely.

**Files:** `src/wikify/cli/work.py` (the `build-evidence` command body).
Add/extend a unit test under `tests/wikify/` asserting the event lands.

**Skill backup (Skill-rewrite phase):** until the code lands, state
prominently in `.claude/skills/wikify-investigate` that the editor MUST
emit `evidence_added` per grown slug (e.g. via `work add evidence
--round`) or the growth-stall gate never advances. After the code fix,
replace that warning with "`build-evidence` self-emits `evidence_added`."

---

## F2. `draft finalize` consumes draft.json — a second call returns `draft_not_found`

**Verified.** `finalize` runs `commit` then `release`
(`src/wikify/cli/draft.py:308-498`). `commit_page`
(`src/wikify/bundle/wiki/commit.py:128`) ends by calling `gc_attempt`
(`commit.py:217`), which unlinks draft.json / response.json /
validation.json (`src/wikify/bundle/draft/artifact.py:60-71`). A second
`finalize` then fails the step-0 existence check and returns
`error="draft_not_found"` at the `normalize-references` step
(`draft.py:412-416`). The behaviour is correct (consume-on-commit) but
the error message reads as "draft was never built," which is misleading
on a re-run.

**Decision: document call-once now; defer idempotency as an issue.**
Consume-on-commit is intentional, so do not change the commit flow.
- **Document (Skill-rewrite phase):** in `.claude/skills/wikify-investigate`
  and `.claude/skills/wikify-write-page` commit guidance, state that
  `draft finalize` is a one-shot that garbage-collects the draft on
  success; a repeat call returning `draft_not_found` means the page was
  already committed, not that the draft is missing.
- **Defer-as-issue:** optional clearer signal — when draft.json is absent
  *and* the page already exists in `wiki.db`, return
  `already_committed`/`already_finalized` instead of `draft_not_found`.
  This is **not** a quick safe fix: it requires a wiki-store lookup inside
  the finalize step-0 guard (`draft.py:393-416`), so file it for a later
  pass rather than the prep commit.

**Files (defer):** `src/wikify/cli/draft.py` finalize step-0 guard;
needs a `wiki.db` page-exists query.

---

## F3. `data add` silently rejects the `chunk:` handle (the form MCP/`corpus show` print)

**Verified, with a correction to the issue wording.** The resolver chain
is `cmd_add` (`src/wikify/cli/data.py:153-165`) ->
`source_text_for` (`src/wikify/data/harvest.py:67`) ->
`read_chunks_by_id` (exact) -> on miss `_resolve_handle`
(`harvest.py:24`) -> `resolve_chunk_id` (`src/wikify/corpus/queries.py:273`)
-> `resolve` (`src/wikify/corpus/handles.py:157`).
`resolve` matches in three tiers: exact full id, `short_id`-equals-short,
and `_<short>` suffix (`handles.py:180-200`). So **full canonical ids and
bare hex shorts already resolve** today. What does NOT resolve is the
**`chunk:`-prefixed handle** — exactly the string `corpus show` /
`format_chunk_handles` emit (`handles.py:234-258`, e.g. `chunk:5f92b0...`)
and the form MCP corpus tools return. `resolve` never strips the `chunk:`
prefix, so all three tiers miss, `source_text_for` returns empty text, and
`verify_point` marks the point `rejected` and silently drops it
(`data.py:161-164`). Note the precedent: `build-evidence`'s own resolver
*does* strip the prefix (`work.py:757`, `short = raw_cid[len("chunk:"):]`),
so the two ingest paths disagree.

**Decision: fix-in-code (quick, safe, do now).**
In `_resolve_handle` (`harvest.py:24`), strip a leading `chunk:` before
calling `resolve_chunk_id` (one line, mirrors `work.py:757`). This makes
`data add` accept the exact handle the agent already has in hand and ends
the silent-reject trap, without touching the shared `resolve` contract.

**Files:** `src/wikify/data/harvest.py` (`_resolve_handle`).
Add a test that `data add` accepts a `chunk:<short>` point.

**Skill backup (Skill-rewrite phase):** in `.claude/skills/wikify-extract-data`,
note that data points must cite a resolvable chunk id; until the fix lands,
pass the bare short or full id, not the `chunk:` handle.

---

## F4. `kind=data` artifacts are a separate store from the wiki page graph

**Verified (matches the data-artifacts-separate-layer design).** Data
artifacts are committed through the data CLI into `DataStore`
(`src/wikify/cli/data.py` `commit`/`consolidate`/`rebuild`, store under
`bundle.root`), not into `wiki.db`. They render and appear in navigation,
but `wiki show` / `wiki traverse` / `wiki find` return
`error="page_not_found"` (`src/wikify/cli/wiki.py:234`, `wiki.py:791`)
because the wiki store never sees them. The round-trip surface is the
data CLI (`data list`, `data show`, `data query`, `data list-artifacts`).

**Decision: document — do NOT register data pages in `wiki.db`.**
The two-store split is deliberate (data tables re-derive from a view spec;
they are not graph nodes). Registering them would conflate the stores.
- **Docs phase:** in the data-layer section (the `docs/investigate.md` /
  data write-up that audit-docs H3 already calls for), state plainly that
  `kind=data` pages render + navigate but are not wiki-graph nodes, and
  that their query/round-trip surface is the `data` CLI noun.
- **Skill-rewrite phase:** add a triage note to
  `.claude/skills/wikify-query` and `.claude/skills/wikify-search-wiki`:
  a `page_not_found` from `wiki show/traverse/find` on a data table is
  expected — fall back to `data show`/`data query`, do not retry on the
  wiki side.

**Files:** `docs/` (data-layer section); `.claude/skills/wikify-query`,
`.claude/skills/wikify-search-wiki`. No code change.

---

## F5. `chunk_coverage_ratio` ~0.90 is structurally impossible; completeness governs the loop

**Verified — the code already documents this.** `coverage.py` states that
references / captions / figures / tables / acknowledgments / appendix /
boilerplate are never cited as evidence and are ~half of a typical
parsed-paper corpus, so the raw ratio cannot approach 1.0; it exposes an
`addressable` denominator (excluding `EXCLUDED_SECTION_TYPES`) as the
meaningful signal (`src/wikify/bundle/work/coverage.py:7-12`, `27-42`,
`CoverageReport` fields `coverage.py:45-55`). This matches the e2e finding
that 0.90 chunk coverage is infeasible and the loop should stop on
completeness.

**Decision: document only (no code change).**
- **Docs phase:** the coverage section of `docs/investigate.md` (and any
  metrics doc) must say: `chunk_coverage_ratio` cannot approach 1.0 by
  construction; the investigate loop is governed by **completeness**, and
  **`addressable_coverage_ratio`** is the coverage signal to read. Do not
  set a chunk-coverage stop target near 0.90.
- **CLAUDE.md:** if the "Current Focus" or any guidance implies a raw
  chunk-coverage target, correct it to the addressable/completeness framing.

**Files:** `docs/` (coverage section), `CLAUDE.md`. No code change; the
`coverage.py` docstring is already the source of truth to mirror.

---

## F6. Two gather paths route telemetry to different model tiers

**Verified.** `build-evidence` (`src/wikify/cli/work.py:648`) is a
deterministic gather — seed-doc chunks plus `corpus find --rank all` with
structural exclusions — and makes **no** per-chunk model calls, so its work
is attributed to the editor/supervisor tier (tier M) and the per-chunk
haiku judge tier is never exercised. The
`.claude/skills/wikify-gather-evidence-cluster` path instead fans out
cheap haiku judges that emit per-chunk routing/score/quote (tier H), then
commits one ledger per slug. Both terminate in the same evidence ledger,
but their telemetry lands on different tiers, so a run dominated by
`build-evidence` shows ~zero haiku usage — expected, not a bug.

**Decision: document only (no code change).**
- **Skill-rewrite phase:** in `.claude/skills/wikify-investigate` and
  `.claude/skills/wikify-gather-evidence-cluster`, describe the two gather
  paths and when each applies: `build-evidence` = cheap deterministic
  gather (no haiku tier exercised, telemetry on tier M); the cluster skill
  = haiku-judge fleet (per-chunk tier H) when model judgment over chunks
  is wanted.
- **Docs phase:** add the same two-path note to the evidence/telemetry
  section of `docs/investigate.md` so the tier distribution in a run is
  interpretable.

**Files:** `.claude/skills/wikify-investigate`,
`.claude/skills/wikify-gather-evidence-cluster`, `docs/`. No code change.

---

### Cross-references to existing audits

- F4 overlaps `audit-docs.md` H3 (data-artifact layer undocumented) — the
  data-layer doc section covers both.
- F5 / F6 belong in the same `docs/investigate.md` that `audit-docs.md`
  H2 calls for (investigate is undocumented in `docs/`).
- F1 and F3 are the only two flagged as quick, safe code fixes; both have
  an in-repo precedent to mirror (`cmd_add_evidence` for F1, the
  `build-evidence` handle resolver `work.py:757` for F3).
