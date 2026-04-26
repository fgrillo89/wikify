---
name: wikify/reference/atoms
description: Compositional v2 atoms — pre/post-conditions for the deterministic verbs that workflow skills compose.
---

# Atoms (v2)

Atomic CLI verbs the skill loop composes. Each atom is deterministic
Python. The skill drives ordering; the atom enforces its own pre /
post conditions.

## ``corpus find --seed``

Pick the greedy submodular seed-doc set.

- **Pre**: corpus has ``vectors.npz`` + ``knowledge_graph.json``.
- **Post**: returns up to ``--max`` doc ids ordered by score.
- **Realization**: ``wikify corpus find --seed --corpus <c> --max <n>
  --pagerank-weight <w>``.

## ``corpus find "<query>"``

Semantic chunk search.

- **Pre**: corpus has vectors.
- **Post**: returns ``score id doc preview`` rows, ranked.
- **Realization**: ``wikify corpus find "<q>" --corpus <c> --top-k <k>``.

## ``work add concept "<title>"``

Create a concept folder.

- **Pre**: ``run/state.json`` present.
- **Post**: ``work/concepts/<slug>/work.md`` exists; idempotent.
- **Realization**: ``wikify work add concept "<title>" --kind
  article|person --aliases <json>``.

## ``work add evidence <concept>``

Append to the concept's evidence ledger.

- **Pre**: concept exists; records file is JSONL of ``EvidenceRecord``.
- **Post**: ``evidence.jsonl`` grows by N records.
- **Realization**: ``wikify work add evidence <slug> --records <path>``.

## ``work claim <concept>``

Atomic per-concept claim with TTL.

- **Pre**: claim absent or stale or held by same owner.
- **Post**: ``.claim`` written; raises on contention (exit 2).
- **Realization**: ``wikify work claim <slug> --owner <id> --ttl-seconds N``.

## ``work tend``

Deterministic housekeeping.

- **Post**: stale claims expired; evidence ledgers deduped; inbox
  drained (evidence_suggestions append to target ledgers;
  concept_suggestions create new concepts; query_feedback /
  merge_suggestions mark ``needs_refine``); ``work/index.md``
  regenerated.
- **Realization**: ``wikify work tend``.

## ``draft build <concept>``

Compile a ``WriteRequest`` to ``work/concepts/<slug>/draft.json``.

- **Pre**: concept exists; corpus is supplied; ``--model-id`` and
  ``--tier`` chosen by the skill.
- **Post**: ``draft.json`` is a valid ``WriteRequest`` envelope.
- **Realization**: ``wikify draft build <slug> --task create|refine
  --corpus <c> --model-id <id> --tier S|M|L``.

## ``draft check <concept>``

Validate ``response.json`` against ``draft.json``.

- **Pre**: ``draft.json`` and ``response.json`` present.
- **Post**: ``validation.json`` written; ``WriteResponse`` Pydantic
  + structural (``_check_wikipedia_structure`` +
  ``_check_figure_mentions``) + quote-grounding checks all
  recorded; non-zero exit if any fails.
- **Realization**: ``wikify draft check <slug>``.

## ``wiki commit <concept>``

Promote a validated response to the canonical wiki page (under the
run lock).

- **Pre**: ``validation.json`` exists with ``ok=true``.
- **Post**: ``wiki/articles/<slug>.md`` (or ``wiki/people/<slug>.md``)
  exists; concept card status set to ``committed``; per-attempt
  artifacts garbage-collected; ``page_committed`` event emitted.
- **Realization**: ``wikify wiki commit <slug>``.

## ``run init`` / ``run close``

Bookend a run. ``run init`` creates the v2 layout and the first
``stage_changed`` event; ``run close`` writes the ``run_closed``
event with the final status.

## ``run lock`` / ``run unlock``

Bundle-wide advisory lock. The CLI uses the lock automatically
inside any mutating verb. Explicit ``wikify run lock`` is for
fencing a longer skill section.
