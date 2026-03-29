# Harness Refactor Plan

Based on "All The Ways Agents Can Be Stupid" (systematicls). Validated against
ScholarForge's current architecture. Prioritized by impact.

## P0: Plan Verification Loop (prevents planning deviations)

**Problem**: Writer may deviate from the plan. Section output isn't checked
against what the plan specified. Deviations compound downstream.

**Fix**: After each section is written, a lightweight check verifies:
- Did the section cover the topics in `section.description`?
- Did it cite the papers listed in `section.source_papers`?
- Is the word count within 30% of `section.target_tokens`?

Implementation: Add a `verify_section()` function in `llm/schemas.py` that
runs Pydantic validators against the plan. This is NOT a separate LLM call —
it's deterministic checks on the output text. If verification fails, the
section is re-generated with the failure reason appended.

```
plan_section → write_section → verify_against_plan → (pass / retry with feedback)
```

**Files**: `llm/schemas.py` (add PlanComplianceValidator), `generate/writer.py`

## P0: Independent Verification Pass (prevents verification laziness)

**Problem**: Self-revision is self-reported. The writer checks its own work.

**Fix**: After all sections are written, spawn a verification step with a
FRESH context (not the bloated writing context) that:
1. Reads the full generated paper
2. Checks coherence across sections (does the conclusion match the intro?)
3. Checks citation consistency (are all [N] markers resolved?)
4. Checks for redundancy (same point made in multiple sections?)
5. Returns a list of issues

If issues found, targeted sections are re-generated.

Implementation: New `generate/verifier.py` with `verify_paper()` function.
Uses a separate `complete()` call with only the paper text + verification
checklist as context (minimal, fresh context — no accumulated section history).

**Files**: NEW `generate/verifier.py`, modify `generate/writer.py`

## P1: Context Compaction for Long Papers (prevents context anxiety)

**Problem**: Rolling 3-section window loses earlier context. By section 8,
the introduction's framing is gone. Paper loses coherence.

**Fix**: Instead of passing raw last-3-sections text, generate a running
**compacted summary** of all sections written so far. This summary is updated
after each section and passed as context to the next.

The compaction is a deterministic operation (not an LLM call):
- Extract the topic sentence from each written section (first sentence)
- List key claims and citation markers used
- ~50 words per section = 500 words for a 10-section paper

This gives the writer awareness of the full paper arc without consuming
the entire context window.

Implementation: Add `_compact_prior_sections()` to `generate/writer.py`.
Replace `"\n\n".join(sections_written[-3:])` with the compacted summary +
the immediately preceding section (full text).

**Files**: `generate/writer.py`

## P1: Pre-Task Context Sufficiency Check (prevents acting on bad info)

**Problem**: Writer starts generating before verifying it has enough context.
If retrieval returned sparse or irrelevant chunks, the output will be poor.

**Fix**: Before writing begins, check:
- Does each planned section have at least 2 source papers assigned?
- Are the source papers actually in the retrieved context?
- Is total context > 2000 tokens? (below this, output quality degrades)

If checks fail, either expand retrieval (use a different strategy) or warn
the user that context is insufficient.

Implementation: Add `_check_context_sufficiency()` to `generate/writer.py`,
called before the section loop.

**Files**: `generate/writer.py`

## P2: N-Plan Selection (prevents short-term thinking)

**Problem**: Planner generates one plan. It might be a quick-fix structure
(5 generic sections) instead of a thoughtful thematic organization.

**Fix**: Generate 3 plans, then score each on:
- Thematic depth (do sections address distinct themes, not overlap?)
- Source paper distribution (are papers spread across sections?)
- Structural fit for the artifact type

Pick the highest-scoring plan. The scoring can be deterministic (no LLM call)
based on section heading uniqueness, paper assignment balance, and word count
distribution.

Implementation: Modify `plan_paper()` to call `complete_structured()` 3 times
with temperature=0.7, then `_score_plan()` picks the best.

**Files**: `generate/planner.py`

## P2: Post-Session Entropy Cleanup (prevents doc/code drift)

**Problem**: After many code changes, docs and tests drift from reality.

**Fix**: After any major code change session, run a cleanup pass:
- Check that docs/project-status.md matches actual CLI commands
- Check that test count in docs matches `pytest --co -q | tail -1`
- Check that module layout in architecture.md matches actual files
- Flag any dead imports or unused functions

Implementation: A script `scripts/check_entropy.py` that runs deterministic
checks and reports issues. Can be wired into a Claude Code hook.

**Files**: NEW `scripts/check_entropy.py`

## Implementation Order

1. Plan verification loop (P0) — deterministic, no new LLM calls
2. Context compaction (P1) — deterministic, improves coherence immediately
3. Pre-task sufficiency check (P1) — deterministic, prevents wasted calls
4. Independent verification pass (P0) — one additional LLM call per paper
5. N-plan selection (P2) — 2 additional LLM calls per generation
6. Entropy cleanup script (P2) — tooling, no runtime impact
