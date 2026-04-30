---
name: wikify-baseline
description: Run the baseline Wikify strategy by composing wikify-search-corpus, wikify-bundle, wikify-write-page, and shared concept-extraction references. Use when building a first-pass wiki from a corpus with a fixed sample and evidence budget.
allowed-tools: Bash(wikify *) Task
---

# wikify-baseline

Baseline is a strategy workflow. It owns sample count, evidence top-k,
writer tier, concurrency, retry policy, and stop conditions. Core
capability skills explain the mechanics.

## Strategy Defaults

- Sampled documents: up to 12 via `corpus sample --strategy diverse`.
- Sample PageRank weight: 0.7.
- Evidence per concept: top 12 retrieved chunks.
- Writer tier: M.
- Extractor tier: S.
- Concurrent writers: up to 4 claimed concepts.
- Claim TTL: 1800 seconds.
- Retry policy: one same-tier retry, then one escalation to L, then
  mark failed.

## Workflow

1. Use `wikify-bundle` to initialize or open the bundle.
2. Use `wikify-search-corpus` to sample diverse documents:

   ```bash
   wikify corpus sample --corpus <corpus> --max 12 --pagerank-weight 0.7
   ```

3. For each sampled document, use `wikify-search-corpus` to read the
   workflow-selected text. This baseline reads full documents unless
   the run configuration narrows the read scope to abstracts or
   introductions.
4. Use `wikify/references/exploration/concept-extraction.md` to extract
   candidate concepts from the observed text.
5. Use `wikify-bundle` to add accepted concepts:

   ```bash
   wikify work add concept "<title>" --kind article|person --aliases '<json>'
   ```

6. For each accepted concept, use `wikify-search-corpus` to retrieve
   evidence. Convert retrieval results to the evidence JSONL contract
   expected by `wikify work add evidence`.
7. Use `wikify-bundle` to append evidence and claim write targets.
8. In parallel, for each claimed concept:

   ```bash
   wikify draft build <slug> --task create --corpus <corpus> --model-id <model> --tier M
   ```

   Then invoke `wikify-write-page` as the writer subagent. The writer
   reads `draft.json` and writes `response.json`.

9. Use `wikify-bundle` to validate and commit:

   ```bash
   wikify draft check <slug>
   wikify wiki commit <slug>
   wikify work release <slug>
   ```

10. Use `wikify-bundle` to tend, rebuild projections, render, evaluate,
    and close.

## Stop Conditions

- All concepts from the initial sample pass are committed or failed.
- A budget-exceeded event is observed.
- The workflow reaches its configured haiku-equivalent budget.

## Does Not Do

- Does not re-enter concept extraction after the initial sample pass.
- Does not perform query-driven refinement.
- Does not hide strategy choices in Python.

## References

- `../wikify-search-corpus/SKILL.md`
- `../wikify-bundle/SKILL.md`
- `../wikify-write-page/SKILL.md`
- `../wikify/references/exploration/concept-extraction.md`
- `../wikify/references/exploration/sampling-patterns.md`
- `../wikify/references/writing/escalation.md`
