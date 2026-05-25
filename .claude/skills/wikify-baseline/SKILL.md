---
name: wikify-baseline
description: Run the canonical end-to-end Wikify baseline from corpus to rendered HTML using simple RAG. Use when building or testing the baseline workflow properly: extractor-driven concept suggestions, optional author/person seeding, evidence gathering, writing, validation, commit, render, eval, inspection, and iteration without hand-curated concept lists.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_schema mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-baseline

Canonical simple-RAG workflow. Owns sample count, concept extraction,
evidence quotas, writer tier, concurrency, retry policy, render/eval
closure, and stop conditions. Core capability skills explain mechanics.

## Workflow (5 phases)

### P1 — Bootstrap

```bash
wikify run init --bundle <bundle> --corpus <corpus> --strategy baseline --target-haiku-eq 25000000
```

Capture `corpus_fingerprint` from stdout — pin it as the manifest hash
in run notes. Then bind MCP and sample:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
mcp__wikify__corpus_sample(strategy="diverse", max_docs=16, pagerank_weight=0.7)
```

`context_show().health` carries doc/chunk counts, detected field, AND
`rank_metrics.{source,author}` — use the author list when seeding
persons (P2). No separate `corpus_schema` round trip needed.

### P2 — Concept extraction + optional persons

Map-reduce over the sampled docs. Full contract in
`../wikify/references/exploration/concept-extraction.md`.

- **Map**: one haiku Task per sampled doc, in parallel. Each Task
  fetches its own doc body via MCP and emits 0–8 candidates from its
  doc only.
- **Reduce**: one sonnet Task (or orchestrator) dedupes by canonical
  title, scores by cross-doc frequency, trims to target.

Append staged concepts and consolidate:

```bash
wikify work add feedback concept --record <bundle>/scratch/concepts_staging.jsonl
wikify work tend
```

Staging path MUST be outside `work/inbox/` (pointing `--record` at
the inbox file re-appends it to itself).

Optionally seed person pages. Pick a populated author rank metric from
`context_show().health.rank_metrics.author`, then:

```
mcp__wikify__corpus_find(by="author", rank="<metric>", top_k=10)
```

Add 3–5 valid authors:

```bash
wikify work add concept "<Display Name>" --kind person --aliases '["author:<key>"]'
```

### P3 — Evidence (vetter per cluster)

```bash
wikify work cluster-concepts --by auto --run <bundle> --format json
```

`--by auto` picks `seeds` pre-evidence and `evidence` post-evidence;
the response's `mode_selected` reports which it chose.

Route each cluster by size:

- **`cluster.size >= 2`** — spawn one `wikify-gather-evidence-cluster`
  Task with `cluster_slugs=<all slugs in cluster>`. The supervisor
  plans one shared query set, fans out haiku judges, and commits one
  ledger per slug. Returns a per-slug envelope dict.
- **`cluster.size == 1`** — spawn one `wikify-gather-evidence` Task
  with that slug. The per-slug path has no fan-out overhead and beats
  the cluster pattern at size 1.

Run cluster Tasks in parallel across clusters of the same kind
(article-clusters parallel with each other, person-clusters parallel
with each other); do not mix article and person clusters in the same
parallel wave since person evidence has different acceptance rules.

Targets per slug (both paths): **≥10 records across ≥5 distinct
docs**, at least one definition chunk. Persons need quoted research
contributions; never invent biography. If a Task returns
`stop_reason="error"` or `appended < 6` for any slug, mark that slug
failed — at most one retry, switching to the per-slug path on the
retry.

### P4 — Write + commit

Re-cluster (chunks now drive grouping):

```bash
wikify work cluster-concepts --by auto --run <bundle> --format json
```

For each cluster:

```bash
for slug in <cluster-slugs>; do
  wikify work claim "$slug" --owner baseline --ttl-seconds 1800
  wikify draft build "$slug" --task create --corpus <corpus> --model-id <model> --tier M --with-adjacent
done
```

Spawn **one** `wikify-write-page` Task per cluster covering all of its
slugs. `--with-adjacent` loads each evidence chunk's flanking ord-1/
ord+1 chunks for synthesis.

Per page, run the single-shot finalize chain (normalize → check →
commit → release):

```bash
wikify draft finalize <slug> --run <bundle> --owner baseline --format json
```

`finalize` short-circuits on the first failure. If `check` fails,
inspect `validation.json` and re-invoke the writer for that slug. **Do
not patch committed markdown directly.**

### P5 — Finalize

```bash
wikify work tend
wikify wiki check
wikify wiki rebuild --run <bundle>
wikify wiki navigation-context --run <bundle> --out <bundle>/derived/navigation_context.json
# Invoke wikify-organize-wiki to write and apply derived/navigation.json.
wikify render --bundle <bundle> --format html
wikify run close --status completed --run <bundle>
wikify eval --bundle <bundle> --corpus <corpus>
```

`wiki rebuild` runs `vectors → indexes → graph` in one process; use
`--skip <kind>` (repeatable) when only some projections changed.

Then run the Inspection Loop and write the Final Report.

## Subagent contracts

| role | tier | skill | inputs | return |
|---|---|---|---|---|
| extractor-map | haiku | this skill (P2) | `doc_handle`, `corpus`, `bundle` | ≤8 candidates JSON (≤400 tok) |
| extractor-reducer | sonnet | this skill (P2) | all map arrays | staging JSONL path |
| vetter (singleton) | sonnet | `wikify-gather-evidence` | `slug`, `run`, `corpus`, `quota=12`, `max_query_rounds=3` | Step-7 JSON (≤300 tok) |
| supervisor (cluster) | sonnet | `wikify-gather-evidence-cluster` | `cluster_slugs`, `run`, `corpus`, `quota_per_slug=12`, `max_query_rounds=3` | per-slug envelope dict (≤600 tok) |
| chunk-judge | haiku | `wikify-gather-evidence-cluster` (judge role) | sibling slugs + batch of ≤8 chunks with text | per-chunk routing+score+quote JSON |
| writer | sonnet M | `wikify-write-page` | cluster slugs + dossier paths | per-slug `response.json` paths |

## Telemetry

Token counts must come from the harness `<usage>` payload delivered at
the Agent tool boundary, not from subagent self-reports. Subagents
cannot reliably introspect their tool-result intake; self-reported
`tokens_in` routinely undershoots the harness total by 5-10x. The
orchestrator reads the harness usage block and records it via:

```bash
wikify run record-calls --from-stdin --run <bundle> --format json <<'EOF'
{"role":"vetter","model_id":"...","tier":"M","tokens_in":12000,"tokens_out":300,"stage":"evidence"}
... one line per Task ...
EOF
```

Stages: `extract`, `evidence`, `write`. Cost curves are invalid
without these events — a hard rule of the workflow. `wikify run close`
warns on stderr if no `call` events exist when closing.

## Defaults

- Sampled documents: 12–16; `corpus sample --strategy diverse`,
  `pagerank_weight=0.7`.
- Target articles: 10–20. Target people: 3–5 when author metadata
  exists.
- Vetter quota: **12** records, max 3 gap-driven query rounds.
- Retrieval: MCP `corpus_find(rank="all", include_text=True)`. The
  `include_text` flag inlines chunk bodies in the search response —
  vetters skip the per-candidate `corpus_show` follow-up. Bash `wikify
  corpus find` is the fallback only when the MCP server is unbound.
- Writer tier: M. Extractor tier: S.
- Concurrent writers: **default 2** (rate-limit safe on Sonnet); raise
  to 4 only after confirming no rate-limit warnings.
- Claim owner: `baseline`. Claim TTL: 1800 seconds.
- Retry: one evidence/writer repair, then mark failed.
- Budget: ~22M haiku-eq on a 12–16-doc baseline (dominated by ~28
  sonnet vetters). `--target-haiku-eq 25000000` tracks headroom;
  informational, not enforced.

## Hard Rules

- Do not bypass `concept_suggestions.jsonl` for the initial concept set.
- Do not call `wikify work build-evidence <slug>` directly (without
  `--from-ids`). That is the pre-vetter seed-first-N-by-ord path and
  ships byline / references-list / off-topic noise. Evidence gathering
  goes through the vetter subagent.
- Do not repair committed pages with ad hoc scripts. Fix the skill,
  evidence, draft, or writer output and re-run the gate.
- Do not call cost curves valid while `type="call"` events are absent.

## Common mistakes

- Choosing the article list by reading sampled titles only (bypasses
  the extractor).
- Using graph-RAG, summaries, rerankers, or query refinement
  (off-protocol for baseline).
- Falling back to bash `wikify corpus find` while MCP is bound — pays
  ~3.6 s cold-start per query.

## Inspection Loop

After render, inspect ≥5 pages:

| page kind | what to check |
|---|---|
| strong central article | lead quality, coverage breadth, citation density |
| weak / thin article | how the writer handled sparse evidence |
| person page (if any) | quoted contributions, no invented biography |
| index / sidebar / search | navigation correctness, link integrity |
| page with equations / chemistry | math and chemical notation rendering |

Iterate (add evidence, refine, add missing concepts; re-render and
re-eval) when weak.

## Stop Conditions

Success criteria pass; budget exhausted; or a deterministic blocker is
documented.

## Final Report (checklist)

- [ ] Bundle + corpus path + `corpus_fingerprint`
- [ ] Sampled doc count, extractor output count, promoted concept count
- [ ] Committed / failed article and person pages
- [ ] Rendered `index.html` path, eval report path
- [ ] Per-page evidence (active records, distinct source docs, used
      references)
- [ ] Eval metrics M1, M3, M5, M6; GT-P / GT-C availability
- [ ] Call-cost telemetry status (must be non-empty)
- [ ] Qualitative site judgment; deterministic blockers + next fix

## References

- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-gather-evidence-cluster/SKILL.md`
- `../wikify-gather-evidence/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/writing/escalation.md`
