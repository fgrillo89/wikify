---
name: gather-evidence
description: Canonical evidence-gathering skill. A sonnet supervisor plans one shared query plan for all sibling slugs in a cluster, fans out cheap haiku judges that read chunks and emit per-chunk routing+score+quote, then commits one evidence ledger per slug. Handles clusters of any size (including singletons).
allowed-tools: Bash(wikify work *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_traverse mcp__wikify__corpus_schema
---

# gather-evidence

You are the supervisor of a fleet of haiku chunk-judges. When the
cluster has 2+ slugs the shared query plan and chunk pool are amortised
across siblings. Singletons (size 1) run through the same contract —
same judge discipline guard, same envelope shape — without the fan-out
savings.

Read/search uses the wikify MCP server (`mcp__wikify__corpus_*`). Keep
the supervisor's context light: in Step 3 you issue searches **without**
chunk text (`include_text=False`) and pass chunk bodies to the judges,
not to yourself. The verbatim-quote and score decisions live in the
judges; the supervisor sees their compact JSON verdicts.

This skill runs on a sonnet-class supervisor. Judges run on haiku.

## Two gather paths

Two paths commit into the same per-slug evidence ledger; pick by whether
you want model judgment over chunks:

- **`wikify work build-evidence`** — a cheap deterministic gather:
  seed-doc chunks plus `corpus find --rank all` with structural
  exclusions. It makes **no** per-chunk model calls, so its work is
  attributed to the supervisor/editor tier (tier M); the haiku judge
  tier is never exercised.
- **This skill** — a haiku-judge fleet (per-chunk tier H) that scores
  and quotes each chunk. Use it when model judgment over chunk content
  is wanted (routing, definition detection, verbatim grounding).

Both terminate in the same `evidence.jsonl`. A run dominated by
`build-evidence` shows ~zero haiku usage — that is expected, not a bug.

## Inputs

- `cluster_slugs` (required) — list of concept slugs in this cluster,
  all article-kind or all person-kind (do not mix).
- `run` (required) — bundle path passed to every CLI call.
- `corpus` (required) — corpus path; bound on the MCP session.
- `quota_per_slug` (default 12) — stop a slug after this many records.
- `max_query_rounds` (default 3) — max gap-driven query iterations
  after the initial plan.
- `judge_batch_size` (default 6) — chunks per haiku judge call. Stays
  well under the quota-16 crack point.
- `max_parallel_judges` (default 8) — concurrent judge Tasks per wave.

## Non-negotiable rules

1. **Supervisor reads no chunk text.** Step 3 calls
   `corpus_find(include_text=False)`. Chunk bodies live in judge
   contexts only. Violating this defeats the cost win.
2. **Judges read every chunk.** Each judge return must include a
   verbatim quote per accepted chunk. The supervisor spot-checks every
   accepted row against the chunk's `text` field; rows without a
   verbatim quote are rejected (judge discipline guard).
3. **One chunk → many slugs allowed.** A chunk supporting both
   `synaptic-plasticity` and `spike-timing-dependent-plasticity` lands
   in both per-slug ledgers. The supervisor de-dupes chunk_ids within
   each ledger but routes one accept to multiple ledgers when the judge
   says so.
4. **Score = topic role, per slug.** The ladder: 1.00 definition, 0.95
   mechanism, 0.85 materials/process, 0.75 application, 0.60
   sibling-relevant. The judge sets the score in the context of the slug
   it routes to; a chunk routed to two slugs may carry two different
   scores.
5. **Definition chunks are gold.** A judge MUST mark `def_for: [slug]`
   when the chunk opens with `<title> is …` / `<title> refers to …` /
   `<acronym> stands for …`. The supervisor inspects per-slug `def_for`
   totals at gap-analysis time and crafts definition-hunters for slugs
   still lacking one.
6. **Quota per slug, not per cluster.** Each slug's ledger may land
   fewer than `quota_per_slug` if the corpus runs out; never pad with
   weak acceptances. Cap any single `section_type` at half the quota per
   slug.

## Step 0: bind the corpus once

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<run>")
mcp__wikify__context_show()
```

## Step 1: read the cluster

The work CLI has no MCP equivalent; bash is fine here.

```bash
wikify work cluster-concepts --by auto --run <run> --format json
for slug in <cluster_slugs>; do
  wikify work show "$slug" --run <run> --format json
done
```

For each slug record: `title`, `aliases`, `seed_doc_handles`, and
whether the slug already has any evidence (so you do not double-fill).
For person slugs, also pull the `author:<key>` alias.

## Step 2: shared query plan

Build a deduped query list across all sibling slugs.

**Mandatory queries (run every cluster run, no exceptions).** For each
slug, add three literal-substring queries with `text=True` so the
semantic ranker does not dilute the exact phrasing the corpus uses for
definitions:

```
corpus_find(query='<title> is',          text=True, top_k=15)
corpus_find(query='<title> refers to',   text=True, top_k=15)
corpus_find(query='<acronym> stands for', text=True, top_k=15)
```

Skip the third when no alias is an acronym. The judge marks hits with
`def_for: [<slug>]` when a returned chunk opens with the matched phrase.
These are the lowest-cost, highest-yield way to land definition chunks.
Do NOT make them conditional on "no committed evidence yet" — the
supervisor needs a definition for the writer's lead even when other
facets are already populated.

**Coverage queries (semantic):**

- One query per unique title.
- One query per unique alias (skip aliases that are substrings of
  another slug's title to avoid redundancy).
- For each seed doc handle (across all slugs), one `in_doc=<handle>`
  query using the most central sibling title.

**Query cap.** Mandatory definition-hunters never count against the cap.
Cap the coverage queries at `2 * cluster_size + 2`; drop the
lowest-value coverage queries (longest aliases, redundant doc-scoped
queries) to fit. Definition coverage beats incrementally broader
semantic recall.

Mark each query with which slugs it is primarily aimed at — judges need
this to route accepts.

## Step 3: issue queries, collect handles only

Base arguments for every search (NOTE: `include_text=False`):

```
by="chunk"
top_k=25
rank="all"
include_text=False
exclude_kinds=["references", "acknowledgments", "figure", "table", "caption", "boilerplate"]
```

For each query, call `mcp__wikify__corpus_find(...)`. Merge results into
one candidate pool keyed on `chunk_id`. Each pool row carries:
`chunk_id`, `doc_handle`, `section_type`, `score`, `preview`
(≤240 chars), and the `targeted_slugs` from the query plan.

The supervisor's context now holds N×≤25 rows of preview-size data
(~100 tokens each) rather than chunk bodies (~500-1500 tokens each).

## Step 4: fan out haiku judges

Partition the candidate pool into batches and spawn one Task per batch,
following the judge protocol in
`references/judge-protocol.md`. The protocol fixes:

- the **batch-size heterogeneity gate** (`cluster_size >= 4` → reduce
  `judge_batch_size` to 4; else keep the configured size),
- the **round-1 sonnet escalation** rule (`failure_rate >= 0.30` across
  the first wave → switch all subsequent batches to sonnet),
- the exact **judge input block** (sibling slug blocks + per-chunk full
  text pre-fetched via `corpus_show(handle="chunk:<short>", full=True)`
  + the scoring ladder),
- the strict **judge output JSON** (`on_topic_for[].{slug,score,quote}`,
  `def_for`, `section_type`, `reject_reason`),
- the mandatory **judge discipline guard** (verify every accepted
  quote is a verbatim, NFKC-normalised substring of the chunk text;
  drop + log failures; sonnet re-run, then abandon, on repeat failure).

Run judges with `Task(model="haiku", run_in_background=True)` in waves
of `max_parallel_judges`. Wall time per wave ≈ slowest judge ≈ 30-60 s.

## Step 5: route accepts to per-slug ledgers

Per slug, build the accept list:

- Walk judge rows. For each `on_topic_for[i].slug == <slug>`, append
  `{chunk_id, score: on_topic_for[i].score, quote: on_topic_for[i].quote}`
  to that slug's ledger.
- De-dupe by `chunk_id` within a ledger; if a judge routed twice to the
  same slug (same chunk_id), keep the higher score.
- Cap each ledger at `quota_per_slug`.
- Cap any single `section_type` at half the quota.
- Drop seeds that produced zero accepts (do not re-query them).

## Step 6: gap-driven rounds

Per slug, ask:

- Is `def_for: [<slug>]` empty for this slug? If yes, queue 1-2
  definition-hunters for it.
- Are accepted `section_type`s diverse enough (≥3 distinct)? If not,
  queue queries that target the missing facet (e.g. "<title>
  applications" for slugs with no body chunks).
- Is the slug's evidence too narrow? Broaden when `distinct_docs < 8`,
  when one document supplies most of the records (over-concentration), or
  when the accepted docs all cluster in one publication-year band (an era
  gap). Queue 1-2 queries that exclude the over-represented doc and
  target under-covered sources and eras — pull in both older/seminal work
  and recent work — so a page consults old AND new papers, not just the
  highest-PageRank few.
- Does the concept have a CONTESTED point (the accepted evidence reports
  competing values or claims) with only ONE side represented, or a
  sub-topic the concept clearly spans that has no accepted chunk yet?
  Queue a query for the alternative/contradictory finding or the missing
  sub-topic, so a page can present the full range rather than one angle.

Aggregate fresh queries across slugs; dedup; cap to 2-4 new queries per
round. Loop back to Step 3 (issue queries → fan out judges → route
accepts). Keep looping while a round still surfaces a NEW DISTINCT DOC or
a new `section_type` facet for a slug — do not stop merely because a fixed
round budget elapsed. Stop when ANY:

- All slugs hit `quota_per_slug`, OR
- Two consecutive rounds add no new distinct doc AND no new section-type
  facet to any slug — a genuine plateau; mark the slug
  `evidence_exhausted` (the WRITE recall gate treats exhausted as
  permission to write despite missing docs), OR
- The safety ceiling `max_query_rounds` is reached — mark `round_cap_hit`
  so the editor re-dispatches next round rather than assuming completeness.

## Step 7: commit per slug

For each slug with `len(accepts) >= 6` (the minimum-viable bar):

```bash
wikify work build-evidence <slug> \
  --from-ids @- \
  --run <run> --corpus <corpus> --format json <<EOF
[
  {"chunk_id": "...", "score": 1.00, "quote": "..."},
  ...
]
EOF
```

`build-evidence` resolves both full canonical chunk ids and
`chunk:<short>` handles, and verifies each `quote` is a literal
substring of the chunk text (ids whose quote is fabricated are rejected
with `rejected_quote_not_in_chunk`). It dedups by chunk_id, so a re-run
or top-up on an existing `evidence.jsonl` is safe.

**`build-evidence` does NOT emit an `evidence_added` event.** The
growth-stall maturity gate keys off `evidence_added` events scoped to
the current round; a slug grown only through `build-evidence` looks
permanently stalled. After this skill commits, the editor MUST emit one
`evidence_added` per grown slug, scoped to the current round (e.g.
`wikify work add evidence <slug> --round <n>`), or the gate never
advances. Surface every committed slug in the return envelope so the
editor can emit the events.

For slugs with `< 6` accepts, do NOT commit; mark them as a workflow
signal in the per-slug envelope (`stop_reason: "pool_exhausted"`,
`appended: <count>`) and let the orchestrator decide whether to re-spawn
this skill as a single-slug top-up. The next invocation will see the
existing `evidence.jsonl` and size its remaining quota accordingly.

## Step 8: return one envelope per slug

The final assistant message MUST contain ONLY the JSON envelope — no
preamble prose, no trailing notes. One `results` entry per slug. Any
deviation (extra fields, renamed keys, alternate shape) is a contract
violation and breaks orchestrator parsing. Narrative notes go inside the
`errors[]` list on the relevant slug, one short string per entry.

The exact schema, per-field semantics, and a worked two-slug example are
in `references/return-envelope.md`. The top-level keys are
`cluster_size`, `queries_issued`, `unique_chunks_judged`, `judge_calls`,
`judge_discipline_failures`, and `results` (a map of slug →
`{appended, distinct_docs, iterations, stop_reason, definition_chunk,
score_tiers, errors}`).

The orchestrator records cost telemetry from the harness `<usage>`
totals at each Task boundary; the supervisor does NOT self-report
tokens.

## Hard rules

- **Scratch files go to temp, never to project dirs.** Judge prompts,
  intermediate payloads, and judge response dumps MUST be written to the
  system temp directory (`/tmp/` on POSIX, `$TEMP` on Windows) or under
  `<run>/scratch/`. NEVER write to `src/`, `.claude/`, or any
  other directory in the project source tree. A clean working tree after
  the cluster run is a workflow invariant.
- Supervisor calls `corpus_find` with `include_text=False`. Always.
- Judges set per-chunk score on the ladder in Rule 4 (1.0 definition,
  0.95 mechanism, …).
- Judge batch size ≤ 8. Larger batches break haiku discipline on the
  quote rule — haiku cuts corners around quota 16. Sonnet reliably
  honors the score / quote / no-false-positive discipline.
- Every supervisor-accepted row carries a verbatim quote from the
  chunk's `text` field, validated post-NFKC.
- Do not commit a slug whose accept list is `< 6`. Surface as a workflow
  signal; the orchestrator may re-spawn this skill on the failed slug as
  a top-up.
- Do not add seeds to work cards; seeds come from the extractor.
- Person slugs gather TWO evidence classes. (1) `contribution` — chunks
  quoting the author's actual work; plain author bylines alone do not
  count. (2) `identity_context` — chunks that NAME the target author AND
  carry affiliation/role/career/collaboration signal ("Department of
  ...", "Professor", "joined ...", "research group"). These are normally
  boilerplate-excluded, but are accepted here BECAUSE they specifically
  name the target author; tag them `note="identity_context"` (cap ~4).
  Identity-context chunks let the page lead with who the person is;
  `wikify work build-evidence`'s person Phase-3 gathers them
  automatically, so the vetter's job is to keep any that surface.

## References

- `references/judge-protocol.md` — full haiku judge contract: batch-size
  gate, sonnet escalation, judge I/O JSON, discipline guard.
- `references/return-envelope.md` — exact return schema, field
  semantics, worked example.
- `../search-corpus/SKILL.md` — corpus_find / corpus_show primitives and
  `include_text` semantics.
- `../bundle/SKILL.md` — `wikify work build-evidence` commit surface.
- `../explore/SKILL.md` — the P1-P5 patterns that feed candidate chunks
  into this vetter.
