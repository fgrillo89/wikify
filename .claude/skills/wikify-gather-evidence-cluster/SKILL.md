---
name: wikify-gather-evidence-cluster
description: Cluster-supervised evidence loop. A sonnet supervisor plans one shared query plan for all sibling slugs in a cluster, fans out cheap haiku judges that read chunks and emit per-chunk routing+score+quote, then commits one evidence ledger per slug. Use when a cluster has 2+ slugs that share corpus material. Singletons use wikify-gather-evidence instead.
allowed-tools: Bash(wikify work *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_traverse mcp__wikify__corpus_schema
---

# wikify-gather-evidence-cluster

You are the supervisor of a fleet of haiku chunk-judges. The cluster's
slugs share corpus material, so one query plan and one chunk pool are
amortised across N slugs instead of paid N times.

Read/search uses the wikify MCP server (`mcp__wikify__corpus_*`). Keep
the supervisor's context light: in Step 3 you issue searches **without**
chunk text (`include_text=False`) and pass chunk bodies to the judges,
not to yourself. The verbatim-quote and score decisions live in the
judges; the supervisor sees their compact JSON verdicts.

This skill runs on a sonnet-class supervisor. Judges run on haiku.
For singleton clusters use `wikify-gather-evidence` instead — the
supervisor pattern adds overhead that does not pay back at size 1.

## Inputs

- `cluster_slugs` (required) — list of concept slugs in this cluster,
  all article-kind or all person-kind (do not mix).
- `run` (required) — bundle path passed to every CLI call.
- `corpus` (required) — corpus path; bound on the MCP session.
- `quota_per_slug` (default 12) — stop a slug after this many records.
- `max_query_rounds` (default 3) — max gap-driven query iterations
  after the initial plan.
- `judge_batch_size` (default 6) — chunks per haiku judge call.
  Stays well under the documented quota-16 crack point.
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
   `synaptic-plasticity` and `spike-timing-dependent-plasticity`
   lands in both per-slug ledgers. The supervisor de-dupes
   chunk_ids within each ledger but routes one accept to multiple
   ledgers when the judge says so.
4. **Score = topic role, per slug.** The ladder is identical to
   `wikify-gather-evidence`: 1.00 definition, 0.95 mechanism, 0.85
   materials/process, 0.75 application, 0.60 sibling-relevant. The
   judge sets the score in the context of the slug it routes to;
   a chunk routed to two slugs may carry two different scores.
5. **Definition chunks are gold.** A judge MUST mark `def_for: [slug]`
   when the chunk opens with `<title> is …` / `<title> refers to …` /
   `<acronym> stands for …`. The supervisor inspects per-slug
   `def_for` totals at gap-analysis time and crafts definition-hunters
   for slugs still lacking one.
6. **Quota per slug, not per cluster.** Each slug's ledger may land
   fewer than `quota_per_slug` if the corpus runs out; never pad with
   weak acceptances. Cap any single `section_type` at half the quota
   per slug.

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

Build a deduped query list across all sibling slugs:

- One query per unique title.
- One query per unique alias (skip aliases that are substrings of
  another slug's title to avoid redundancy).
- For each seed doc handle (across all slugs), one
  `in_doc=<handle>` query using the most central sibling title.
- For each slug with no committed evidence yet, two
  definition-hunters: `corpus_find(query="<title> is", text=True)`
  and `corpus_find(query="<title> refers to", text=True)`.
- Cap initial plan at `2 * (cluster_size + 2)` queries. Drop the
  lowest-value ones (longest aliases, redundant doc-scoped queries)
  to fit.

Mark each query with which slugs it is primarily aimed at — judges
need this to route accepts.

## Step 3: issue queries, collect handles only

Base arguments for every search (NOTE: `include_text=False`):

```
by="chunk"
top_k=25
rank="all"
include_text=False
exclude_kinds=["references", "acknowledgments", "figure", "table", "caption", "boilerplate"]
```

For each query, call `mcp__wikify__corpus_find(...)`. Merge results
into one candidate pool keyed on `chunk_id`. Each pool row carries:
`chunk_id`, `doc_handle`, `section_type`, `score`, `preview`
(≤240 chars), and the `targeted_slugs` from the query plan.

The supervisor's context now holds N×≤25 rows of preview-size data
(~100 tokens each) rather than chunk bodies (~500-1500 tokens each).

## Step 4: fan out haiku judges

Partition the pool into batches of `judge_batch_size` chunks. For
each batch, spawn one haiku Task. The judge's contract:

**Judge input (you supply in the prompt):**
- Sibling slugs with `{slug, title, aliases, definition_priors?}` —
  one tiny block per slug.
- The batch's chunk handles + their full text. The supervisor
  pre-fetches text by calling
  `mcp__wikify__corpus_show(handle="chunk:<short>", full=True)` once
  per chunk in the batch, caches the result, and passes the texts
  inline in the judge's prompt. `corpus_find` cannot be scoped by
  `chunk_id` — only `in_doc` (one document handle) is available —
  so the per-chunk `corpus_show` is the only reliable path.
- The scoring ladder (same as Rule 4 above).

**Judge output (strict JSON, ≤400 tokens):**
```json
[
  {
    "chunk_id": "<id>",
    "on_topic_for": [
      {"slug": "<sibling-slug>", "score": 0.95, "quote": "<verbatim sentence from chunk text>"}
    ],
    "def_for": ["<slug>"],
    "section_type": "<type>"
  },
  {
    "chunk_id": "<id>",
    "on_topic_for": [],
    "reject_reason": "byline / references-list / off-topic / boilerplate"
  }
]
```

Run judges with `Task(model="haiku", run_in_background=True)` in waves
of `max_parallel_judges`. Wall time per wave ≈ slowest judge ≈ 30-60 s.

### Judge discipline guard (mandatory)

For each accepted row the supervisor receives:

1. Fetch the chunk's `text` once (cache it).
2. Verify the judge's `quote` is a verbatim substring (post-NFKC
   normalise both sides to dodge Unicode-confusable rejections).
3. If the quote is missing or not present, drop that accept row and
   log a `judge_discipline_failure` event.
4. If a judge batch returns ≥2 discipline failures, re-run that batch
   once with `Task(model="sonnet")` as a fallback. Sonnet is more
   expensive but reliable on the quote rule.
5. If the sonnet re-run also returns ≥2 discipline failures, discard
   the entire batch's accepts, log a `judge_batch_abandoned` event
   naming the chunk_ids, and continue. Do not retry a third time. A
   batch that fails twice usually means the chunks themselves are
   malformed (OCR garbage, byline-only, references-list dump that
   the section classifier missed); padding the ledger from them
   would degrade dossier quality.

## Step 5: route accepts to per-slug ledgers

Per slug, build the accept list:

- Walk judge rows. For each `on_topic_for[i].slug == <slug>`, append
  `{chunk_id, score: on_topic_for[i].score, quote: on_topic_for[i].quote}`
  to that slug's ledger.
- De-dupe by `chunk_id` within a ledger; if a judge routed twice to
  the same slug (same chunk_id), keep the higher score.
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
- Is `distinct_docs < 5` for this slug? If yes, queue 1 query that
  excludes the over-represented doc via in-context filtering.

Aggregate fresh queries across slugs; dedup; cap to 2-4 new queries
per round. Loop back to Step 3 (issue queries → fan out judges →
route accepts). Stop when ANY:

- All slugs hit `quota_per_slug`, OR
- `max_query_rounds` exhausted, OR
- Latest round added zero accepts to any slug.

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

For slugs with `< 6` accepts, do NOT commit; mark them as a workflow
signal and let the orchestrator decide whether to retry with the
per-slug `wikify-gather-evidence` path.

## Step 8: return one envelope per slug

Final response is a JSON object keyed by slug (≤600 tokens total):

```json
{
  "cluster_size": <int>,
  "queries_issued": <int>,
  "unique_chunks_judged": <int>,
  "judge_calls": <int>,
  "judge_discipline_failures": <int>,
  "results": {
    "<slug>": {
      "appended": <int>,
      "distinct_docs": <int>,
      "iterations": <int>,
      "stop_reason": "quota_met" | "max_rounds" | "pool_exhausted" | "error",
      "definition_chunk": true | false,
      "score_tiers": <int>,
      "errors": []
    }
  }
}
```

The orchestrator records cost telemetry from the harness `<usage>`
totals at each Task boundary; the supervisor does NOT self-report
tokens.

## Hard rules

- Supervisor calls `corpus_find` with `include_text=False`. Always.
- Judges set per-chunk score on the same ladder as
  `wikify-gather-evidence` (1.0 definition, 0.95 mechanism, ...).
- Judge batch size ≤ 8. Larger batches break haiku discipline on the
  quote rule (documented in `wikify-gather-evidence` lines 19-25).
- Every supervisor-accepted row carries a verbatim quote from the
  chunk's `text` field, validated post-NFKC.
- Do not commit a slug whose accept list is `< 6`. Surface as a
  workflow signal; the per-slug path may rescue it.
- Do not add seeds to work cards; seeds come from the extractor.
- Person slugs: judges accept chunks that quote actual contributions
  by that author; author bylines alone do not count.
- For cluster_size == 1, do NOT use this skill. Switch to
  `wikify-gather-evidence`.

## When to fall back to per-slug

The orchestrator should pick the per-slug skill when:

- The cluster has exactly 1 slug.
- Two cluster-mode runs in a row hit `judge_discipline_failures > N/2`
  for the same cluster (the corpus may have noisy chunk bodies that
  haiku trips on).
- The supervisor exceeds 60 s on Step 2/3 (most likely indicates a
  malformed cluster).

## References

- `../wikify-gather-evidence/SKILL.md` — the per-slug path; contracts
  for chunk vetting, the score ladder, decision-row format, and
  judge anti-patterns live there. This cluster skill is a fan-out
  layer on top, not a replacement.
- `../wikify-search-corpus/SKILL.md` — corpus_find / corpus_show
  primitives and `include_text` semantics.
- `../wikify-bundle/SKILL.md` — `wikify work build-evidence` commit
  surface.
