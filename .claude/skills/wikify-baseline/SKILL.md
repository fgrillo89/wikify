---
name: wikify-baseline
description: Run the canonical end-to-end Wikify baseline from corpus to rendered HTML using simple RAG. Use when building or testing the baseline workflow properly: extractor-driven concept suggestions, optional author/person seeding, evidence gathering, writing, validation, commit, render, eval, inspection, and iteration without hand-curated concept lists.
allowed-tools: Bash(wikify *) Task
---

# wikify-baseline

Baseline is the canonical simple-RAG workflow. It owns sample count,
concept extraction, evidence top-k, writer tier, concurrency, retry
policy, render/eval closure, and stop conditions. Core capability
skills explain mechanics.

## Success Criteria

Minimum complete baseline:

- fresh bundle with `run/state.json`;
- pinned corpus path and manifest hash recorded in the run notes;
- extractor-produced `work/inbox/concept_suggestions.jsonl`;
- `work tend` promotes accepted article/person concepts;
- 10-20 committed article pages, unless a blocker is documented;
- at least 3 attempted person pages when author metadata exists;
- every committed page passes `draft check` and `wiki commit`;
- `wikify wiki check` passes;
- `wikify render --bundle <bundle> --format html` succeeds;
- rendered site is inspected for navigation, references, math/equations,
  and quality;
- `wikify run close --status completed` runs before final eval;
- final `wikify eval --bundle <bundle> --corpus <corpus>` is written
  after close;
- unresolved failures are named as blockers, not hidden.

## Hard Rules
- Do not choose the article list by reading sampled titles only.
- Do not bypass `concept_suggestions.jsonl` for the initial concept set.
- Do not use graph-RAG, summaries, rerankers, or query refinement.
- Do not repair committed pages with ad hoc scripts as the normal path.
  Fix the skill, evidence, draft, or writer output and re-run the gate.
- Do not call cost curves valid while `type="call"` events are absent.

## Defaults

- Sampled documents: 12-16 via `corpus sample --strategy diverse`.
- Sample PageRank weight: 0.7.
- Target articles: 10-20.
- Target people: 3-5 when author metadata exists.
- Evidence per page: gather 20-30 candidates, then keep 12-18 active
  records after quality filtering.
- Retrieval: `corpus find --rank all`.
- Writer tier: M.
- Extractor tier: S.
- Concurrent writers: up to 4 claimed concepts.
- Claim owner: `baseline`.
- Claim TTL: 1800 seconds.
- Retry: one evidence/writer repair, then mark failed.

## Workflow

1. Initialize a fresh bundle:

   ```bash
   wikify run init --bundle <bundle> --corpus <corpus> --strategy baseline --target-haiku-eq <n>
   ```

2. Pin the corpus:

   - record exact corpus path;
   - record manifest hash;
   - run `wikify corpus check --corpus <corpus>`.

3. Sample documents:

   ```bash
   wikify corpus sample --corpus <corpus> --max 16 --pagerank-weight 0.7
   ```

4. Materialize sampled text for extraction. For each sampled doc
   handle, read enough body text for real extraction, not only titles:

   ```bash
   wikify corpus show <doc-handle> --corpus <corpus> --full --run <bundle>
   ```

5. Spawn an extractor Task. Give it the sampled text plus
   `../wikify/references/exploration/concept-extraction.md`. Require
   JSONL records shaped for the concept inbox:

   ```json
   {"title":"Atomic Layer Deposition","kind":"article","aliases":["ALD"],"quote":"verbatim sampled text","definition":"...","score":0.91}
   ```

   Extractor rules:

   - output article and person candidates;
   - include a verbatim quote from observed text;
   - include `author:<key>` in aliases for any person candidate whose
     corpus author handle is resolvable;
   - prefer fewer high-value concepts;
   - flag duplicates or merge ambiguity;
   - do not invent a canonical topic list from memory.

6. Append and consolidate concept suggestions:

   ```bash
   wikify work add feedback concept --record <concept_suggestions.jsonl>
   wikify work tend
   wikify work list
   ```

7. Seed person pages from author metadata when available. Run author
   rankings. If citation and h-index values are all zero, use
   `n_papers` as the deterministic seed:

   ```bash
   wikify corpus find --by author --rank n_papers --top-k 10 --corpus <corpus> --run <bundle>
   wikify corpus find --by author --rank citation_count --top-k 10 --corpus <corpus> --run <bundle>
   wikify corpus find --by author --rank h_index --top-k 10 --corpus <corpus> --run <bundle>
   ```

   Add 3-5 valid authors as `kind=person` concepts unless already
   suggested by the extractor:

   ```bash
   wikify work add concept "<Display Name>" --kind person --aliases '["author:<key>"]'
   ```

8. Gather evidence per concept. Use the CLI: it consumes the extractor's
   `seed_doc_handles` as a precision prior, then tops up via
   `corpus find` to reach the quota, applying the boilerplate filter,
   never-cite regex, and per-doc cap deterministically.

   ```bash
   wikify work build-evidence <slug> \
     --corpus <corpus> --run <bundle> \
     --target 14 --top-k 40 --per-doc-cap 3
   ```

   Articles: ≥12-14 active records across ≥5 source docs. Person pages
   reuse the same command; the seed pass naturally pulls from the
   author's own papers when those handles are in `seed_doc_handles`.
   Persons require grounded research contributions / publications;
   never invent biography.

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

After render, inspect the site and at least 5 pages, including: one
strong central article, one weak/thin article, one person page (if any),
the index/sidebar/search surface, and one page with equations or
chemical notation.

Assess title/scope, lead quality, mechanism/materials/applications
coverage, citation density and reference readability, math/chem
rendering, frontmatter `links` population, and whether person pages
avoid invented biography. If weak, iterate by adding evidence,
refining pages, or adding missing concepts; re-render and re-eval.

## Stop Conditions

- Success criteria pass.
- The configured budget is exhausted.
- A deterministic blocker prevents completion and is documented.

## Final Report

Report bundle path, corpus path and manifest hash, sampled doc count,
extractor output count, promoted concept count, committed/failed
article and person pages, exact rendered site `index.html` path, eval
report path, evidence gathered per committed page (active records,
distinct source docs, and used reference definitions), M1, M3, M5, M6,
GT-P/GT-C availability, call-cost telemetry status, qualitative site
judgment, deterministic blockers, and next fix.

## References

- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify-organize-wiki/SKILL.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/writing/escalation.md`
