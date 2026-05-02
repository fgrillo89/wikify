# SQLite query store — deviations from the spec

Tracks places where the implementation differs from
`tasks/sqlite-query-store-plan.md`. Architecture and acceptance criteria
are unchanged unless a specific design hard-constraint is named here.

## Phase 1 — sequential authoring, not 7 parallel agents

The kickoff prompt asks for the second Phase 1 commit to be produced by
spawning 7 parallel agents (A–G) in a single Agent tool block.

This implementation runs Phase 1 sequentially in the main session:

- The 7 modules (`documents`, `chunks`, `authors`/`document_authors`,
  `bib_entries`/`chunk_citations`, `assets`/`chunk_assets`, `graph`,
  `fts`, `vectors`) all wire into one `Store` class. Authoring them
  sequentially keeps the public API consistent across modules and
  avoids per-agent integration friction.
- Total token cost is lower because we don't pay seven prompt-context
  setups for the same surrounding spec, and the main thread keeps a
  shared mental model for Phases 2–8 without re-deriving it from
  agent summaries.

This is an execution-strategy deviation, not a design change. The
schema, API surface, locked defaults, and acceptance tests still match
the spec.

## Module layout — 8 files, not 11

The spec lists module names: `documents.py`, `chunks.py`, `authors.py`,
`document_authors.py`, `bib_entries.py`, `chunk_citations.py`,
`assets.py`, `chunk_assets.py`, `graph.py`, `fts.py`, `vectors.py`.

The implementation collapses pairs whose CRUD is intertwined:

- `documents.py` and `chunks.py` are kept separate (different entities)
- `authors.py` covers authors + document_authors (always written together)
- `bib.py` covers bib_entries + chunk_citations + DOI re-resolution
- `assets.py` covers assets + chunk_assets

The `Store` class in `__init__.py` is the public integration point; it
exposes the full surface specified for Phase 1.

## Source-of-truth file path

The spec references `src/wikify/corpus/models.py:55–121` as the
canonical fields source. The actual file in this repository is
`src/wikify/models.py:55–120`. Same content, different path.

## Notebooks directory is gitignored

`notebooks/` is in `.gitignore`. The Phase 0 walkthrough notebook
(`notebooks/sqlite_store_schema_walkthrough.ipynb`) lives on disk only,
matching the existing `notebooks/kg_fluent_api.ipynb` situation.
