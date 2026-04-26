---
name: wikify/reference/schemas
description: Durable artifact catalog and schema_version policy for the v2 wikify file contract.
---

# Schemas (v2)

Every durable file the agent or CLI produces has a documented schema
and a monotonically increasing ``schema_version`` integer. Pydantic
models in ``src/wikify/`` are the executable source of truth; this
file is the catalog.

## Schema_version policy

- Every durable artifact carries ``schema_version: int``.
- Bump on a breaking field change: removed key, renamed key, changed
  type, changed semantics.
- Non-breaking additions (new optional field) do not bump.
- Skills assert ``schema_version == N`` before reading. If ahead,
  fail fast with ``SchemaVersionMismatchError`` rather than guess.

## Run artifacts

### ``<bundle>/run/state.json``

Source of truth: ``src/wikify/bundle/run/state.py::RunStateV1``.
Current ``schema_version``: 1.

Required fields:

- ``schema_version: int``
- ``run_id: str``
- ``status: "active" | "completed" | "failed" | "abandoned"``
- ``strategy: str`` — free-form workflow label; the agent picks
- ``corpus_path: str``, ``wiki_path: str``, ``work_path: str``
- ``budget: {target_haiku_eq, spent_haiku_eq}``
- ``stages: dict[str, "pending"|"running"|"done"|"failed"]``
- ``created_at: str``, ``updated_at: str``

Owning CLI: ``wikify run init/show/set/close``. Mutated under
``run/lock``.

### ``<bundle>/run/events.jsonl``

Append-only structured event ledger. One JSON object per line.
Source of truth: ``src/wikify/bundle/run/events.py::Event``.

Required envelope: ``schema_version``, ``event_id``, ``run_id``,
``type``, ``at``, ``actor``, ``data``. Optional indexing fields:
``concept_id``, ``page_id``, ``chunk_id``, ``doc_id``, ``stage``.

Allowed event types: ``stage_changed``, ``cli_invoked``,
``concept_created``, ``concept_status_changed``, ``chunk_read``,
``evidence_added``, ``inbox_suggestion_created``,
``inbox_consolidated``, ``query_started``, ``wiki_page_read``,
``query_feedback_created``, ``draft_created``, ``call``,
``validation_completed``, ``page_committed``, ``page_refined``,
``budget_exceeded``, ``run_closed``.

### ``<bundle>/run/lock``

Atomic file lock with TTL. Owned by ``bundle/run/lock.py``.
Acquisition: ``os.open(O_CREAT|O_EXCL)`` for fresh; ``os.replace``
for stale-reclaim. Contents: ``{owner, pid, acquired_at,
expires_at, ttl_seconds}``.

### ``<bundle>/run/io/<event_id>.{stdin,stdout,stderr}.txt``

CLI IO transcripts. Per ``cli_invoked`` event. Captured by
``src/wikify/cli/_io.py``.

## Work artifacts (per concept)

### ``<bundle>/work/concepts/<slug>/work.md``

ControlCard: YAML frontmatter + freeform body. Frontmatter source:
``src/wikify/bundle/work/card.py::WorkCard``.

Frontmatter fields: ``page_id``, ``kind: article|person``,
``status``, ``aliases``, ``evidence_chunks``, ``evidence_docs``,
``new_evidence_since_commit``, ``needs_refine``, ``last_compacted``,
``wiki_path`` (when committed).

### ``<bundle>/work/concepts/<slug>/evidence.jsonl``

Append-only evidence ledger. One ``EvidenceRecord`` per line.
Source: ``src/wikify/bundle/work/evidence.py::EvidenceRecord``.

Fields: ``chunk_id``, ``doc_id``, ``quote``, ``score``, ``status``
(``active``|``archived``), ``used_in_page``, ``note``.

### ``<bundle>/work/concepts/<slug>/.claim``

Per-concept advisory claim. Atomic. Source:
``src/wikify/bundle/work/claim.py``.

### ``<bundle>/work/inbox/{evidence_suggestions, concept_suggestions, merge_suggestions, query_feedback}.jsonl``

Append-only cross-talk channels. Drained by ``wikify work tend``.
Source: ``src/wikify/bundle/work/inbox.py``.

## Per-attempt artifacts (transient, gc'd post-commit)

### ``<bundle>/work/concepts/<slug>/draft.json``

Model-facing ``WriteRequest`` payload. Canonical:
``src/wikify/schema.py::WriteRequest``. Carries the
``schema_version`` envelope. Created by ``wikify draft build``.

### ``<bundle>/work/concepts/<slug>/response.json``

Writer subagent's raw ``WriteResponse``. Canonical:
``src/wikify/schema.py::WriteResponse``.

### ``<bundle>/work/concepts/<slug>/validation.json``

Validation verdict. Source:
``src/wikify/bundle/draft/validator.py``. Fields: ``schema_version``,
``ok``, ``page_id``, ``response_path``, ``draft_path``, ``errors``,
``structural_checks``, ``checked_at``.

## Wiki artifacts

### ``<bundle>/wiki/articles/<slug>.md`` and ``<bundle>/wiki/people/<slug>.md``

Wikipedia-style page markdown with YAML frontmatter. Subdirectory is
determined by ``page_kind``. Frontmatter required: ``id``, ``kind``,
``title``, ``aliases``. Body rules: ``write-constraints.md``.
Citation format: ``citation-format.md``. Owning CLI:
``wikify wiki commit``.

### ``<bundle>/derived/index.json``

Generated index over ``wiki/`` (slugs + paths + kinds). Owning
command: ``wikify wiki build indexes``.

### ``<bundle>/derived/graph.json``

Serialised wiki knowledge graph (cite-edge graph). Owning command:
``wikify wiki build graph``.

### ``<bundle>/derived/vectors.npz``

Per-page embeddings used by ``wiki find`` semantic search. Owning
command: ``wikify wiki build vectors``.
