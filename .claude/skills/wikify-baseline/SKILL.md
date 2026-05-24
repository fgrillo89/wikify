---
name: wikify-baseline
description: Run the canonical end-to-end Wikify baseline from corpus to rendered HTML using simple RAG. Use when building or testing the baseline workflow properly: extractor-driven concept suggestions, optional author/person seeding, evidence gathering, writing, validation, commit, render, eval, inspection, and iteration without hand-curated concept lists.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_schema mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-baseline

Baseline is the canonical simple-RAG workflow. It owns sample count,
concept extraction, evidence quotas, writer tier, concurrency, retry
policy, render/eval closure, and stop conditions. Core capability
skills explain mechanics.

## Workflow (5 phases)

### P1 — Bootstrap

```bash
wikify run init --bundle <bundle> --corpus <corpus> --strategy baseline --target-haiku-eq 25000000
```

Capture `corpus_fingerprint` from stdout — this IS the manifest hash to
pin in run notes.

Then bind the MCP session and confirm:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
mcp__wikify__corpus_sample(strategy="diverse", max_docs=16, pagerank_weight=0.7)
```

`context_show` reports doc/chunk counts, derived artifacts, detected
field, and folds in the use case of `wikify corpus check`. For
populated author rank metrics (used in P2 person seeding), call
`mcp__wikify__corpus_schema()` and read `rank_metrics.author`.

### P2 — Concept extraction (map-reduce + optional persons)

Concept extraction runs as **map-reduce** over the sampled docs. Each
map Task fetches its own doc body via MCP; the orchestrator never
holds doc text. Full contract:
`../wikify/references/exploration/concept-extraction.md`.

- **Map**: one haiku Task per sampled doc, in parallel. Each emits 0–8
  candidates from its doc only.
- **Reduce**: one sonnet Task (or the orchestrator) dedupes by
  canonical title, scores by cross-doc frequency, trims to target.

Record telemetry (see "Telemetry" below).

Append staged concepts to the inbox and consolidate:

```bash
wikify work add feedback concept --record <bundle>/scratch/concepts_staging.jsonl
wikify work tend
wikify work list
```

The staging path MUST be outside `work/inbox/`. Pointing `--record` at
the inbox file itself re-appends it to itself.

Optionally seed person pages. Pick a populated author rank metric (from
`corpus_schema().rank_metrics.author`), then:

```
mcp__wikify__corpus_find(by="author", rank="<metric>", top_k=10)
```

Add 3–5 valid authors:

```bash
wikify work add concept "<Display Name>" --kind person --aliases '["author:<key>"]'
```

### P3 — Evidence (vetter per cluster)

Cluster by seed overlap to bound writer context:

```bash
wikify work cluster-concepts --by seeds --run <bundle> --format json
```

For each cluster, spawn one `wikify-gather-evidence` Task per slug
**in parallel within the cluster**. Run waves serially across
clusters. See "Subagent contracts" below for the spawn shape.

Targets per slug: **≥10 records across ≥5 distinct docs**, at least
one definition chunk. Person slugs need quoted research
contributions; never invent biography. If a vetter returns
`stop_reason="error"` or `appended < 6`, mark the slug failed and
continue — at most one retry.

### P4 — Write + commit (writer per cluster)

Re-cluster post-evidence (chunks now drive the grouping):

```bash
wikify work cluster-concepts --run <bundle> --format json
```

For each cluster:

```bash
for slug in <cluster-slugs>; do
  wikify work claim "$slug" --owner baseline --ttl-seconds 1800
  wikify draft build "$slug" --task create --corpus <corpus> --model-id <model> --tier M --with-adjacent
done
```

Then spawn **one** `wikify-write-page` Task per cluster covering all
of its slugs. `--with-adjacent` loads each evidence chunk's flanking
ord-1/ord+1 chunks for synthesis.

Per page, normalize → validate → commit → release:

```bash
wikify draft normalize-references <slug>
wikify draft check <slug>
wikify wiki commit <slug>
wikify work release <slug> --owner baseline
```

`normalize-references` deterministically projects the `## References`
block from evidence so quote-grounding is correct by construction. If
validation fails, inspect `validation.json` and re-invoke the writer
for that slug. **Do not patch committed markdown directly.**

### P5 — Finalize

```bash
wikify work tend
wikify wiki check
wikify wiki build vectors
wikify wiki build indexes
wikify wiki build graph
wikify wiki navigation-context --run <bundle> --out <bundle>/derived/navigation_context.json
# Invoke wikify-organize-wiki to write and apply derived/navigation.json.
wikify render --bundle <bundle> --format html
wikify run close --status completed --run <bundle>
wikify eval --bundle <bundle> --corpus <corpus>
```

Then run the Inspection Loop and write the Final Report.

## Subagent contracts

### Extractor (P2 map)

- Subagent: haiku, skill `wikify-baseline` map-reduce section.
- Inputs per Task: `doc_handle`, `corpus`, `bundle`.
- Return: JSON array of ≤8 candidates (≤400 tokens).

### Extractor reducer (P2)

- Subagent: sonnet (or orchestrator inline).
- Inputs: all map JSON arrays.
- Return: staging JSONL path under `<bundle>/scratch/`.

### Vetter (P3, one per slug)

- Subagent: sonnet, skill `wikify-gather-evidence`.
- Inputs: `slug`, `run`, `corpus`, `quota=12`, `max_query_rounds=3`.
- Return: Step-7 JSON envelope (≤300 tokens). Flag any vetter that
  returns prose.

### Writer (P4, one per cluster)

- Subagent: sonnet (tier M), skill `wikify-write-page`.
- Inputs: cluster slugs + per-slug dossier paths.
- Return: per-slug `response.json` paths.

## Telemetry

Record one call per Task return, in the same loop iteration that
spawned the Task:

```bash
wikify run record-call --run <bundle> --role <role> \
  --model-id <model> --tier <tier> \
  --tokens-in <n> --tokens-out <n> --stage <extract|evidence|write>
```

Roles: `extractor-map`, `extractor-reducer`, `vetter`, `writer`. Cost
curves are invalid without these events — a hard rule of the workflow.

## Defaults

- Sampled documents: 12–16 via `corpus sample --strategy diverse`,
  `pagerank_weight=0.7`.
- Target articles: 10–20.
- Target people: 3–5 when author metadata exists.
- Vetter quota: **12** records, max 3 gap-driven query rounds.
- Retrieval: MCP `corpus_find` with `rank="all"`. Bash `wikify corpus
  find` is only the fallback for environments without the MCP server.
- Writer tier: M. Extractor tier: S.
- Concurrent writers: **default 2** (rate-limit safe on Sonnet);
  raise to 4 only after confirming no rate-limit warnings.
- Claim owner: `baseline`. Claim TTL: 1800 seconds.
- Retry: one evidence/writer repair, then mark failed.
- Budget: ~22M haiku-eq on a 12–16-doc baseline (dominated by ~28
  sonnet vetters). `--target-haiku-eq 25000000` tracks headroom; the
  field is informational, not enforced.

## Hard Rules

- Do not bypass `concept_suggestions.jsonl` for the initial concept
  set.
- Do not call `wikify work build-evidence <slug>` directly (without
  `--from-ids`). That is the pre-vetter, seed-first-N-by-ord path and
  ships byline / references-list / off-topic noise. Evidence
  gathering goes through the vetter subagent.
- Do not repair committed pages with ad hoc scripts as the normal
  path. Fix the skill, evidence, draft, or writer output and re-run
  the gate.
- Do not call cost curves valid while `type="call"` events are absent.

## Common mistakes (anti-patterns)

- Choosing the article list by reading sampled titles only (bypasses
  the extractor).
- Using graph-RAG, summaries, rerankers, or query refinement
  (off-protocol for baseline).
- Falling back to bash `wikify corpus find` while the MCP server is
  bound — pays ~3.6 s cold-start per query.

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

Success criteria pass; budget exhausted; or a deterministic blocker
is documented.

## Success criteria

- Fresh bundle with corpus pinned (`corpus_fingerprint` in run notes).
- Extractor-produced `work/inbox/concept_suggestions.jsonl`; `work
  tend` promotes the accepted concepts.
- 10–20 committed article pages plus 3+ attempted persons when author
  metadata exists.
- Every committed page passes `draft check` and `wiki commit`.
- `wikify wiki check` passes.
- `wikify render --format html` succeeds; the rendered site is
  inspected.
- `wikify run close --status completed` runs before the final
  `wikify eval`.
- `type="call"` events recorded for every extractor / vetter / writer
  Task (no telemetry gap).
- Unresolved failures named as blockers, not hidden.

## Final Report (checklist)

- [ ] Bundle + corpus path + `corpus_fingerprint`
- [ ] Sampled doc count
- [ ] Extractor output count
- [ ] Promoted concept count
- [ ] Committed / failed article and person pages
- [ ] Rendered `index.html` path
- [ ] Eval report path
- [ ] Per-page evidence (active records, distinct source docs, used
      references)
- [ ] Eval metrics M1, M3, M5, M6
- [ ] GT-P / GT-C availability
- [ ] Call-cost telemetry status (must be non-empty)
- [ ] Qualitative site judgment
- [ ] Deterministic blockers + next fix

## References

- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-gather-evidence/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/writing/escalation.md`
