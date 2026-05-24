---
name: wikify-baseline
description: Run the canonical end-to-end Wikify baseline from corpus to rendered HTML using simple RAG. Use when building or testing the baseline workflow properly: extractor-driven concept suggestions, optional author/person seeding, evidence gathering, writing, validation, commit, render, eval, inspection, and iteration without hand-curated concept lists.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_schema mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse mcp__wikify__wiki_schema
---

# wikify-baseline

Baseline is the canonical simple-RAG workflow. It owns sample count,
concept extraction, evidence top-k, writer tier, concurrency, retry
policy, render/eval closure, and stop conditions. Core capability
skills explain mechanics.

## Success Criteria

Fresh bundle with corpus pinned (path + manifest hash in run notes);
extractor-produced `work/inbox/concept_suggestions.jsonl`; `work tend`
promotes the accepted concepts; 10-20 committed article pages plus 3+
attempted persons when author metadata exists; every committed page
passes `draft check` and `wiki commit`; `wikify wiki check` passes;
`wikify render --format html` succeeds; the rendered site is inspected
for navigation, references, math, and quality; `wikify run close
--status completed` runs before the final `wikify eval`; unresolved
failures are named as blockers, not hidden.

## Hard Rules
- Do not choose the article list by reading sampled titles only.
- Do not bypass `concept_suggestions.jsonl` for the initial concept set.
- Do not use graph-RAG, summaries, rerankers, or query refinement.
- Do not call `wikify work build-evidence <slug>` directly (without
  `--from-ids`). That is the pre-vetter, seed-first-N-by-ord path which
  ships byline / references-list / off-topic noise into the dossier.
  Evidence gathering goes through the `wikify-gather-evidence` vetter
  subagent (Step 8).
- Do not repair committed pages with ad hoc scripts as the normal path.
  Fix the skill, evidence, draft, or writer output and re-run the gate.
- Do not call cost curves valid while `type="call"` events are absent.

## Defaults

- Sampled documents: 12-16 via `corpus sample --strategy diverse`.
- Sample PageRank weight: 0.7.
- Target articles: 10-20.
- Target people: 3-5 when author metadata exists.
- Evidence per page: vetter quota 14 records, max 3 gap-driven query
  rounds. The vetter handles candidate sourcing internally — do not
  pre-set top-k or per-doc cap from this layer.
- Retrieval: MCP `corpus_find` with `rank="all"`. Bash `wikify corpus
  find` is only the fallback for environments without the MCP server.
- Writer tier: M.
- Extractor tier: S.
- Concurrent writers: up to 4 claimed concepts (contractual cap). On
  rate-limited Sonnet accounts the practical safe parallelism is 2;
  the smoke run lost a writer wave to a per-minute limit at 3-way
  parallel. Drop to 2 if you see the limit warning.
- Claim owner: `baseline`.
- Claim TTL: 1800 seconds.
- Retry: one evidence/writer repair, then mark failed.
- Budget: observed envelope on a 12-16-doc baseline is ~22M haiku-eq
  (dominated by 28 sonnet vetters). Pass `--target-haiku-eq 25000000`
  to `wikify run init` to track headroom, or leave at 0 — the field
  is informational, not enforced.
- Concept extraction: parallel map (haiku per doc) + sonnet reducer.
  Map batches of 1 doc per haiku Task; reduce in one sonnet Task.

## Workflow

1. Initialize a fresh bundle (bash, mutation):

   ```bash
   wikify run init --bundle <bundle> --corpus <corpus> --strategy baseline --target-haiku-eq <n>
   ```

2. Bind the MCP session to the corpus + bundle and pin:

   ```
   mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
   mcp__wikify__context_show()   # confirms doc/chunk counts, derived artifacts, detected field
   ```

   `context_show` folds in the use case of `wikify corpus check`; no
   separate health call. Record corpus path + manifest hash in the
   run notes.

3. Sample documents (MCP):

   ```
   mcp__wikify__corpus_sample(strategy="diverse", max_docs=16, pagerank_weight=0.7)
   ```

4. (No orchestrator-side text read.) The orchestrator does NOT
   pre-fetch doc bodies. Map extractors fetch their own assigned doc
   body via MCP, keeping body text out of the orchestrator's context.

5. Concept extraction is a **map-reduce**: one haiku Task per sampled
   doc (parallel map), then a sonnet/orchestrator pass that dedupes
   and scores by cross-doc frequency. Full contract in
   `../wikify/references/exploration/concept-extraction.md` (Map-reduce
   orchestration section). Per map Task and once for the reduce,
   record telemetry via `wikify run record-call --stage extract`.

6. Append staging concepts to the inbox and consolidate:

   ```bash
   wikify work add feedback concept --record <scratch/concepts_staging.jsonl>
   wikify work tend
   wikify work list
   ```

   The staging path must be OUTSIDE `work/inbox/`. Pointing
   `--record` at the inbox file itself re-appends it to itself.

7. Seed person pages from author metadata. Read
   `health.available_metrics.author` from `context_show()` to see
   which author metrics are populated, then rank by a populated one:

   ```
   mcp__wikify__corpus_find(by="author", rank="<populated metric>", top_k=10)
   ```

   Add 3-5 valid authors (bash — concept mutation):

   ```bash
   wikify work add concept "<Display Name>" --kind person --aliases '["author:<key>"]'
   ```

8. Gather evidence via `wikify-gather-evidence` (sonnet subagents).

   Pre-cluster by seed overlap, then spawn one vetter per slug
   concurrently within each cluster:

   ```bash
   wikify work cluster-concepts --by seeds --run <bundle> --format json
   ```

   `--by seeds` works pre-evidence (default `--by evidence` is used
   later in Step 9). Run waves serially across clusters to bound
   orchestrator context; one wave for small concept sets.

   Each vetter Task: subagent type sonnet, skill
   `wikify-gather-evidence`, inputs `slug` / `run` / `corpus` /
   `quota=14` / `max_query_rounds=3`. Vetters return ONLY the Step 7
   JSON envelope (≤300 tokens); flag any vetter that returns prose.

   After each vetter returns, record (bash — telemetry mutation):

   ```bash
   wikify run record-call --run <bundle> --role vetter \
     --model-id <model> --tier M \
     --tokens-in <n> --tokens-out <n> --stage evidence
   ```

   Targets: articles ≥10-14 appended across ≥5 distinct docs, at
   least one definition chunk per article. Person pages need quoted
   research contributions; never invent biography.

   If a vetter returns `stop_reason="error"` or `appended < 6`, mark
   the slug failed and continue — do not retry more than once. Each
   vetter operates on its own slug, so concurrent vetters never
   collide on `evidence.jsonl`.

9. Cluster concepts, claim, draft, write:

   ```bash
   wikify work cluster-concepts --run <bundle> --format json
   ```

   Clusters group concepts that share evidence docs (Jaccard ≥ 0.15)
   so one writer agent can handle a coherent topical neighborhood with
   no duplicate chunk reads. Persons go in their own cluster.

   For each cluster, claim its concepts, build drafts, and spawn ONE
   writer agent that handles all pages in that cluster:

   ```bash
   for slug in <cluster-slugs>; do
     wikify work claim "$slug" --owner baseline --ttl-seconds 1800
     wikify draft build "$slug" --task create --corpus <corpus> \
       --model-id <model> --tier M --with-adjacent
   done
   # Spawn one wikify-write-page agent per cluster.
   ```

   `--with-adjacent` loads each evidence chunk's flanking ord-1/ord+1
   chunks into `context_window` for synthesis. After each Task returns
   token usage:

   ```bash
   wikify run record-call --run <bundle> --role writer \
     --model-id <model> --tier M \
     --tokens-in <n> --tokens-out <n> --stage write
   ```

10. Normalize, validate, commit, release. The writer's prose is what
    matters; `normalize-references` deterministically projects the
    `## References` block from `evidence[N-1]` so quote-grounding is
    correct by construction:

    ```bash
    wikify draft normalize-references <slug>
    wikify draft check <slug>
    wikify wiki commit <slug>
    wikify work release <slug> --owner baseline
    ```

    If validation fails, inspect `validation.json`, then repair through
    the normal work/draft/write path (re-invoke the writer for that
    slug). Do not patch committed markdown directly.

11. Build projections, render, close, and eval:

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

## Inspection Loop

After render, inspect ≥5 pages: one strong central article, one
weak/thin article, one person page (if any), the index/sidebar/search
surface, and one page with equations or chemical notation. Assess
title/scope, lead quality, coverage, citation density, math/chem
rendering, frontmatter `links`, and whether person pages avoid
invented biography. Iterate (add evidence, refine, add missing
concepts; re-render and re-eval) when weak.

## Stop Conditions

Success criteria pass; budget exhausted; or a deterministic blocker
is documented.

## Final Report

Bundle + corpus path + manifest hash; sampled doc count; extractor
output count; promoted concept count; committed/failed article and
person pages; rendered `index.html` path; eval report path; per-page
evidence (active records, distinct source docs, used references);
M1, M3, M5, M6; GT-P/GT-C availability; call-cost telemetry status;
qualitative site judgment; deterministic blockers; next fix.

## References

- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/writing/escalation.md`
