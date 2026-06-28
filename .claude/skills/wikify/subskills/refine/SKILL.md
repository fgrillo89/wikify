---
name: refine
description: Refinement workflow that consolidates bundle inbox feedback, finds concepts marked needs_refine, gathers additional evidence when required, rewrites pages, validates, and commits replacements. Use when refinement threshold, batch, and retry policies are supplied.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse
---

# refine

Apply accumulated inbox feedback and repair committed pages through the
normal write, validate, and commit gate. Committed pages are repaired
only here; do not rewrite a committed page outside this workflow.

This workflow owns the refinement threshold, batch policy, and
retry/escalation. Run it only with explicit target selection and retry
rules supplied by the caller.

## Required inputs

- `bundle` (run path) and `corpus` (corpus path).
- Refinement threshold: how much inbox pressure on a slug justifies a
  rewrite this pass.
- Batch policy: how many slugs to claim and rewrite concurrently.
- Retry and escalation rules: what to do on validation failure, claim
  contention, and writer escalation.

## Setup

Bind the bundle and corpus on the MCP session first:

```
mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")
mcp__wikify__context_show()
```

Read and search go through the wikify MCP server throughout. Bundle
mutations stay on the `wikify` bash CLI.

## Procedure

1. Consolidate the inbox and surface targets.

   ```bash
   wikify work tend --run <bundle>
   ```

   `work tend` drains `query_feedback.jsonl` (marks each affected page
   `needs_refine`) and `merge_suggestions.jsonl` (marks both sides
   `needs_refine`). It does not write pages; it only flags work cards.

2. List the flagged cards and apply the refinement threshold to pick
   this pass's batch (see the `bundle` skill for inbox/work-state
   inspection commands). Drop slugs below the threshold.

3. For each target, grow evidence only when the existing dossier is too
   thin to resolve the feedback. Choose a gather path (see **Evidence
   growth policy**). Skip this step when current evidence already covers
   the requested change.

4. Claim the concept, then build the refine draft.

   ```bash
   wikify work claim <slug> --run <bundle> --owner refine
   wikify draft build <slug> --task refine --corpus <corpus> --run <bundle>
   ```

   Use a single, consistent `--owner` (`refine`) for the whole pass; the
   finalize step must release under the same owner.

5. Write the replacement page with the `write-page` skill, consulting
   `../write-page/references/refinement-style.md`. Return a complete
   `WriteResponse`, not a diff; the commit gate promotes whole pages.

6. Validate and commit through the per-page chain.

   ```bash
   wikify draft finalize <slug> --run <bundle> --owner refine
   ```

   `draft finalize` runs normalize -> validate -> commit -> release in
   order and short-circuits on the first failure, naming the failing
   step. Then refresh projections (see the `bundle` skill
   `commit-and-projections` reference for `wiki build indexes|graph|
   vectors` and `wiki check`).

7. After a committed batch of at least five pages, run `organize-wiki`.
   If fewer pages changed, run it once before the final render.

## Evidence growth policy

Two gather paths land evidence in the same per-slug ledger but exercise
different model tiers. Pick per target:

- **Deterministic gather** (`wikify work build-evidence <slug>`): seed
  doc chunks plus `corpus find --rank all` with structural exclusions.
  No per-chunk model calls, so the work attributes to the supervisor
  tier; a refine pass dominated by this path shows near-zero haiku
  usage. Prefer it when the feedback names a concrete gap that
  structural retrieval can fill and model judgment over chunks is not
  needed.
- **Judge-fleet gather** (`gather-evidence` subagents): a supervisor
  fans out cheap haiku judges that read chunks and emit per-chunk
  routing, score, and verbatim quote, then commits one ledger per slug
  (per-chunk haiku tier). Prefer it when the gap needs model judgment
  over chunk content, or when refining a cluster of sibling slugs that
  amortise one shared query plan.

Both paths commit through `build-evidence`, which dedups by `chunk_id`,
so re-running on a slug with an existing ledger tops up rather than
duplicating.

## Retry and escalation policy

- `draft finalize` is a one-shot that garbage-collects the draft on
  success. A repeat finalize on the same slug returns
  `draft_not_found` because the draft was consumed; that means the page
  was already committed, not that the draft is missing. Do not blindly
  retry finalize. On a mid-chain failure, read the named failing step
  and resume from there (rebuild the draft only if commit never ran).
- On `claim_held`, the slug is owned by another worker; skip it this
  pass rather than forcing the claim.
- On validation failure, route the writer-side fix back through
  `write-page`; escalate the writer tier per the supplied rules and
  `../reference/references/writing/escalation.md`.

## Strategy owned here

- Refinement threshold.
- Evidence growth policy (which gather path, and when to grow at all).
- Batch concurrency.
- Retry and escalation policy.

## References

- `../bundle/SKILL.md`
- `../search-corpus/SKILL.md`
- `../gather-evidence/SKILL.md`
- `../organize-wiki/SKILL.md`
- `../write-page/references/refinement-style.md`
- `../reference/references/writing/escalation.md`
