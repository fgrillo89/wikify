# Wikify Skill Restructure

## Decision

Develop for Claude Code first. The canonical project skill tree is:

```text
.claude/skills/
```

Do not hand-maintain a parallel `.agents/skills/` tree. If Codex support
is needed later, generate an export from `.claude/skills/`.

## Goal

Separate capability surfaces from strategy:

```text
core capability skills -> workflow skills -> benchmarkable strategies
```

Core skills explain how to search, write, and mutate state. Workflow
skills decide what to do next, what to spend, which model tier to use,
how much parallelism to allow, and when to stop.

## Implemented Structure

```text
.claude/skills/
  wikify/
    SKILL.md
    references/
      bundle/
        layout.md
        state.md
        events-ledger.md
        locking-and-claims.md
      cli/
        grammar.md
        output-contract.md
        exit-codes.md
      writing/
        schemas.md
        citation-format.md
        write-constraints.md
        tiers.md
        escalation.md
        field-guides/
          generic.md
          biology.md
          computer-science.md
          materials-science.md
          mathematics.md
          medicine.md
          physics.md
          social-sciences.md
      exploration/
        concept-extraction.md
        sampling-patterns.md
        workflow-contracts.md

  wikify-search-corpus/
  wikify-search-wiki/
  wikify-write-page/
  wikify-bundle/

  wikify-baseline/
  wikify-guided-explore/
  wikify-query/
  wikify-refine/
```

## Core Skills

### `wikify-search-corpus`

Explains the corpus CLI as the read/search surface over the corpus
fluent API. Covers listing, showing, semantic search, text search, seed
selection, and recursive graph traversal patterns. It is read-only and
does not decide exploration strategy.

### `wikify-search-wiki`

Explains committed-wiki lookup: page search, text search, show page,
coverage inspection, relationship lookup when exposed, and bridges from
wiki pages back to corpus evidence. It does not mutate bundle state.

### `wikify-write-page`

Owns the writer contract. Given supplied context and evidence, it
produces a `WriteResponse`. It covers article/person/refinement styles,
optional compaction/editor-brief stages, citation rules, and field guide
layering. It does not validate or commit.

### `wikify-bundle`

Owns mechanical bundle operations: run/work/draft/wiki state, claims,
locks, events, draft validation, commit gates, projections, render, eval,
and failure handling. It does not decide strategy.

## Workflow Skills

Workflow skills own:

- sampling pattern
- loop shape
- readiness thresholds
- evidence budget
- model tier and model id
- writer concurrency
- retry/escalation
- stop conditions

Current workflows:

- `wikify-baseline`
- `wikify-guided-explore`
- `wikify-query`
- `wikify-refine`

## Prompt Migration

The old `src/wikify/prompts/` assets map into skill references as
follows:

```text
extract.yaml
  -> wikify/references/exploration/concept-extraction.md

write.yaml
style_guide.md
artifact_types/wiki_article.md
artifact_types/wiki_person.md
  -> wikify-write-page/references/

edit.yaml
  -> wikify-write-page/references/editor-brief.md

compact.yaml
  -> wikify-write-page/references/compaction.md

query.yaml
  -> wikify-query/references/answer-synthesis.md

fields/*.md
  -> wikify/references/writing/field-guides/
```

Python may keep deterministic loaders temporarily where code still needs
packaged prompt resources. Strategy and prompt policy belong in skills.

## Field Guide Rule

Writers always load:

```text
wikify-write-page/references/style-guide.md
wikify/references/writing/field-guides/generic.md
```

If the workflow, bundle state, corpus metadata, or field detection
identifies a field with confidence, the writer loads exactly one
additional matching field guide. Do not load all field guides.

## Open Decisions

1. Whether old detailed graph references should be reintroduced under
   `wikify-search-corpus/references/` and
   `wikify-search-wiki/references/`.
2. Whether field guide text should be canonical in `.claude/skills` or
   generated from `src/wikify/prompts/fields`.
3. Whether unfinished workflows should remain discoverable stubs or move
   into shared references until executable.
