# Wikify — Agent Contract

Canonical project reference for any agentic runtime. Behaviour rules
(planning, simplicity, blast radius, corrections, etc.) live in
`CLAUDE.md` — read that first.

---

## Read First

1. `docs/architecture.md` — architecture (canonical reference)
2. `docs/filesystem-state-design.md` — filesystem and CLI grammar
3. `docs/metrics.md` — M1–M6, GT-P, GT-C
4. `.claude/skills/wikify/references/` — agent-facing reference (schemas,
   CLI grammar, citation format, write constraints, tiers, escalation,
   knowledge graph, wiki graph)

Historical workstream records live under
`tasks/skill-centric-redesign-plan.md` and
`docs/skill-centric-execution-plan.md` for design rationale only;
the current pipeline does not refer to them.

---

## Product

- **Input**: source documents (PDF, DOCX, PPTX, HTML, MD) ingested into a corpus (`data/corpora/`).
- **Process**: skill-driven workflow seeds pages from the knowledge graph, extracts evidence, canonicalises concepts, writes wiki pages, validates citations.
- **Output**: wiki bundle on disk (`data/wikis/<bundle>/wiki/`) rendered to static HTML by `wikify render`.

Corpus is authoritative evidence. Wiki pages are authoritative
human-facing output. Telemetry (`run/events.jsonl`) is first-class —
strategies, prompts, and costs are compared over time.

---

## Runtime model

The agent runtime — Claude Code or any other agent harness — drives
the workflow. The agent reads skill markdown, calls deterministic CLI
tools via Bash, and spawns model-calling subagents via Task. Python
never calls a model SDK directly.

- Skills own per-iteration loop shape, stopping criteria, model tiers,
  and budget allocation.
- Files are the agent–backend interface. CLI tools read inputs from
  named files and write outputs to named files. The agent passes
  paths, not blobs.
- Durable state lives on disk. `<bundle>/run/state.json` carries
  identity, strategy, paths, budget, and stage status across subagent
  boundaries; `run/events.jsonl` is the append-only event ledger.

---

## Boundaries

Top-level packages (post-Phase-C layout):

- `corpus/` — input corpora: chunks, vectors, doc markdown, images, equations, bibliography, fluent KG (`graph.py`/`graph_build.py`), seed selection, field detection. Read-only during a wiki run.
- `citations/` — citation parsing, BibTeX, DOI/Crossref/OpenAlex resolution. Standalone; consumed by `ingest/` only.
- `bundle/` — everything that lives inside one wiki bundle:
  - `bundle/run/` — execution control: `state.py` (RunState), `events.py` (Event envelope + append/iter), `lock.py` (atomic file lock with TTL), `cost.py` (TierPrice + aggregation from events), `lifecycle.py` (init/close).
  - `bundle/work/` — in-flight build state: `card.py` (work.md ControlCard), `evidence.py` (evidence.jsonl ledger), `inbox.py` (cross-talk channels), `claim.py` (per-concept claim files), `tend.py` (consolidate inbox + dedup + index regen), `dossier.py`, `canonicalize.py`.
  - `bundle/draft/` — per-attempt artifacts: `artifact.py` (draft/response/validation IO), `builder.py` (DraftBuilder), `validator.py` (Validator).
  - `bundle/wiki/` — committed pages + projections: `page.py`, `embeddings.py`, `page_naming.py`, `graph.py` (fluent wiki KG), `commit.py` (the gate), `derived.py` (rebuild_index/graph/vectors), `queries.py` (list/find/show).
- `ingest/` — corpus pipeline (parse, chunk, embed, graph).
- `eval/` — metric computations (M1/M3/M5/M6, GT-P, GT-C).
- `render/` — static site generation.
- `prompts/` — Python-side prompt templates assembled by DraftBuilder.
- `cli/` — argv glue: `__init__.py` (Typer app), `__main__.py`, `_io.py` (`cli_invoked` event capture), `_helpers.py` (exit codes, error envelope), and one file per noun (`corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`).
- `api.py` — `Bundle` and `Corpus` context dataclasses. A bundle is any directory with a `run/` (and ultimately `run/state.json`); `Bundle.open` enforces that.
- `bundle/draft/schema.py` and `bundle/work/schema.py` — Pydantic write/extract contracts (`WriteRequest`, `WriteResponse`, structural checks).

Dependency rules:

- `corpus/` and `citations/` do not depend on `bundle/`.
- `bundle/*` packages do not import each other directly except through `Bundle`.
- `eval/` and `render/` consume bundle state, never modify it.
- `cli/<noun>.py` is a thin adapter — translates argv to one or two domain calls.
- Strategy lives in skills, not Python: no Python class owns budget splits, evidence top-k, model tier, or loop shape. Python exposes deterministic primitives only (`corpus find`, `corpus.queries.select_evidence_chunks`, etc.). Workflow shape and stopping criteria live in `.claude/skills/wikify*/SKILL.md`.

---

## Data Layout

```
data/
  corpora/    ingested corpora
  wikis/      wiki bundles (layout: run/, work/, wiki/, derived/)
  papers/     input source documents
  test_runs/  test run outputs
```

Bundle layout (per-bundle):

```
<bundle>/
  run/
    state.json
    events.jsonl
    lock
    io/
      <event_id>.{stdin,stdout,stderr}.txt
  work/
    index.md
    inbox/{evidence,concept,merge,query_feedback}_suggestions.jsonl
    concepts/<slug>/
      work.md
      evidence.jsonl
      .claim
      draft.json          (transient, gc'd post-commit)
      response.json       (transient)
      validation.json     (transient)
  wiki/
    articles/<slug>.md
    people/<slug>.md
    index.md
  derived/
    index.json
    graph.json
    vectors.npz
```

---

## CLI

Seven nouns. Run under `uv run`. Full grammar in
`docs/filesystem-state-design.md`.

```bash
wikify corpus  build / refresh / check / list / find / show / repl
wikify run     init / show / list events / lock / unlock / close / set
wikify work    list / show / add concept / add evidence / add feedback / set / claim / release / tend
wikify draft   build / show / check
wikify wiki    list / find / show / repl / build / check / commit
wikify render  --bundle <b> --format html [--out <dir>]
wikify eval    --bundle <b> [--corpus <c>] [--report <p>]
```

Bundle resolution. `corpus`, `run`, `work`, `draft`, and `wiki`
accept `--run <bundle>`; otherwise the current working directory must
be a bundle root (with `run/state.json` present). `render` and `eval`
use `--bundle <bundle>` since they are downstream consumers and never
resolve through `run/state.json`.

All mutations on a bundle are gated by the run lock or per-concept
claim.

Exit codes: 0 success, 1 validation/precondition, 2 lock/claim held,
3 budget exceeded, 4 stale claim broken by `work tend`.

## Skill layout

Skills live in `.claude/skills/`. Claude Code is the canonical target
for now; do not hand-maintain a parallel `.agents/skills/` tree.

- **Shared reference** under `.claude/skills/wikify/` — project-wide
  background knowledge loaded by every other skill. Carries
  `references/` for bundle state, CLI grammar, writing schemas,
  citation format, field guides, exploration patterns, and workflow
  contracts.
- **Core capability skills** expose reusable surfaces without owning
  strategy:
  - `wikify-search-corpus` — corpus CLI read/search and graph traversal
    patterns.
  - `wikify-search-wiki` — committed wiki lookup and wiki-to-corpus
    bridge patterns.
  - `wikify-write-page` — writer contract, page styles, and optional
    compaction/editor-brief references.
  - `wikify-bundle` — mechanical bundle operations: run/work/draft/wiki
    state, validation, commit, projections, render, eval, locks, events,
    and failures.
- **Workflow skills** encode strategy: loop shape, sampling pattern,
  stopping criteria, parallelism, budget, model tier, and retry policy.
  Current workflows are `wikify-baseline`, `wikify-guided-explore`,
  `wikify-query`, and `wikify-refine`.

A new strategy is a new workflow skill, not new Python.

---

## Key Vocabulary

| Term | Location | Notes |
|---|---|---|
| `ModelTier` (S / M / L) | `types.py` | Tier vocabulary; use `tier.value` for strings |
| `Role` | `types.py` | extractor / compactor / editor / writer / orchestrator |
| `RunState` | `bundle/run/state.py` | Durable on-disk run state |
| `Event` | `bundle/run/events.py` | Append-only events.jsonl envelope |
| `LockHeldError` | `bundle/run/lock.py` | Run-level lock contention |
| `ClaimHeldError` | `bundle/work/claim.py` | Per-concept claim contention |
| `WriteRequest` / `WriteResponse` | `schema.py` | Pydantic v2 contracts (`extra="forbid"`) |
| `Bundle` / `Corpus` | `api.py` | Path-conventions context dataclasses |

---

## Writer / Page Rules

- **Titles**: natural Wikipedia style (`Atomic Layer Deposition`, not `concept-atomic-layer-deposition`). The id IS the title; `kind` distinguishes page type.
- **Articles**: full Wikipedia-style encyclopedic prose — not stubs. Sections are guidance, not strict requirements.
- **No visible `[[wikilinks]]` in body prose.** Cross-links live in the `links: list[str]` field on `WikiPage`.
- **Person pages**: written in Wikipedia voice. `author_context` carries metadata (publications, citations, coauthors). The phrase "appears in this corpus" is banned. Degrades gracefully if `author_context` is missing.

---

## Citation grounding

- `[^eN]` markers in prose resolve 1:1 to `[^eN]:` definitions in the `## References` block.
- Each `[^eN]:` definition carries `<chunk_id> (<doc_id>) > "<quote>"`.
- The `<quote>` is a verbatim substring of the cited chunk's source text. `wikify draft check` enforces this; `wikify wiki commit` re-checks under the run lock.

A fabricated quote echoed in the body but absent from the source chunk
fails validation; the page never reaches `wiki/`.

---

## Error Handling

- Validation failures (`ValidationError`, `QuoteNotInChunkError`) surface through `wikify draft check` as `validation.json` with `ok=false`. The skill retries once; on second failure escalates per `escalation.md`; on third marks the concept `failed`.
- Mutating commands acquire the run lock or per-concept claim. Lock contention exits 2; claim contention exits 2; budget overrun exits 3; stale-claim broken by `work tend` exits 4 on the affected commands; validation/precondition failure exits 1.
- No bare `except`, no silent `pass`. Failures are logged or re-raised — never hidden.

---

## Data-Handling Principles

1. **One canonical surface per cross-cutting concern.** Extend the existing lookup / classifier / telemetry path; don't fork it.
2. **Source text is sacred; the query is not.** Normalise the query to fit the corpus; leave source text untouched so provenance stays intact.
3. **Convert at the boundary; assert at storage.** Convert once at the seam (e.g. 0- vs 1-based, raw vs normalised). Callers must not guess.
4. **User-controlled input is ground truth.** Filenames, tags, front matter, passed-in parameters beat inferred values. Validate extractions against them; reject mismatches loudly.
5. **Per-field merge, not per-record.** When two sources disagree, the winner is decided per field.
6. **Bidirectional edges are emitted both ways at build time.** Downstream code does not infer the reverse.
7. **State for cross-run comparison is persisted explicitly.** Static approximations of stateful signals invalidate comparisons.
8. **Refactors are complete or not done.** When a schema, CLI noun, path, or concept is renamed, update code, tests, prompts, docs, and skills in the same change; grep for old names and transitional `v1`/`v2`/`legacy` wording before declaring the work complete.
