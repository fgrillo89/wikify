# Wikify — Agent Contract

Canonical project reference for any agentic runtime. Behavior rules
(planning, simplicity, blast radius, corrections, etc.) live in
`CLAUDE.md` — read that first.

---

> **Note:** This document is being rewritten as part of the
> skill-centric redesign. The "Read First" pointers, "Boundaries"
> map, and "Data Layout" stanzas reflect the post-W0 package layout
> (`bundle/`, `corpus/`, `citations/`, `cli/`); the "CLI", "Writer
> / Page Rules", "Citation grounding", "Error Handling", and
> "Data-Handling Principles" sections still describe the legacy
> surface (`session`/`kg`/`extract`/`draft`/`validate`/`bundle`/
> `meter`) which continues to work via `cli/legacy/*` until Phase C.
> The binding redesign target is in
> `docs/skill-centric-execution-plan.md` and
> `docs/filesystem-state-design.md`. Full rewrite lands in Phase D.

## Read First

1. `docs/skill-centric-execution-plan.md` — redesign brief (binding)
2. `docs/filesystem-state-design.md` — target filesystem and CLI surface
3. `docs/architecture.md` — current/legacy system design
4. `docs/metrics.md` — M1–M6, GT-P, GT-C
5. `.claude/skills/wikify/workflows/run-baseline.md` — the canonical workflow loop
6. `.claude/skills/wikify/reference/schemas.md` — durable artifact catalog

---

## Product

- **Input**: source documents (PDF, DOCX, PPTX, HTML, MD) ingested into a corpus (`data/corpora/`).
- **Process**: skill-driven workflow seeds pages from the knowledge graph, extracts evidence, canonicalises concepts, writes wiki pages, validates citations.
- **Output**: wiki bundle on disk (`data/wikis/`) rendered to static HTML by `wikify html`.

Corpus is authoritative evidence. Wiki pages are authoritative
human-facing output. Telemetry (`_run.json`, `_calls.jsonl`) is
first-class — strategies, prompts, and costs are compared over time.

---

## Runtime model

The agent runtime — Claude Code or any other agent harness — drives
the workflow. The agent reads skill markdown, calls deterministic CLI
tools via Bash, and spawns model-calling subagents via Task. Python
never calls a model SDK directly.

- Skills own the per-iteration loop. `.claude/skills/wikify/workflows/run-baseline.md` documents the page-by-page loop.
- Files are the agent–backend interface. CLI tools read inputs from named files and write outputs to named files. The agent passes paths, not blobs.
- Durable state lives on disk. `<bundle>/_session/session.json` carries strategy, budget, stage status, and per-page status across subagent boundaries.

---

## Boundaries

Top-level packages (post-W0 layout):

- `corpus/` — input corpora: chunks, vectors, doc markdown, images, equations, bibliography, fluent KG (`graph.py`/`graph_build.py`), seed selection, field detection. Read-only during a wiki run.
- `citations/` — citation parsing, BibTeX, DOI/Crossref/OpenAlex resolution. Standalone; consumed by `ingest/` only.
- `bundle/` — everything that lives inside one wiki bundle:
  - `bundle/run/` — execution control (state, events, lock, cost). Populated in W2.
  - `bundle/concepts/` — in-flight build state (dossier today; card/evidence/inbox/claim/tend/canonicalize land in W4).
  - `bundle/draft/` — per-attempt artifacts (author_context today; schema/builder/validator/artifact land in W5).
  - `bundle/wiki/` — committed pages, indices, page graph, embeddings, slug naming. `commit.py` and `derived.py` land in W6/W7.
- `ingest/` — parse, chunk, embed, graph, citations, manifest.
- `eval/` — metrics (M1–M6, GT-P, GT-C); consumes wiki bundles, never mutates them.
- `render/` — static site generation; consumes wiki bundles, never mutates them.
- `prompts/` — Python-side prompt templates (writer / extract / refine / artifact / field guides). Assembled by `bundle/draft/builder.py`; not loaded by skills directly.
- `cli/` — argv glue.
  - `cli/__init__.py` — top-level Typer app and noun registrations.
  - `cli/_io.py` — `cli_invoked` event capture wrapper.
  - `cli/_helpers.py` — exit codes, error envelope.
  - `cli/legacy/` — Phase A home for the seven legacy nouns (`session`, `kg`, `extract`, `draft`, `validate`, `bundle`, `meter`); deleted in Phase C.
  - `cli/<noun>.py` files for `corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`, `migrate` land in W1–W8.
- `api.py` — `Bundle` and `Corpus` context dataclasses (replace the legacy `paths.py`); lands in W1.
- Top-level Phase C deletion targets (still on disk during Phase A): `paths.py`, `schema.py`, `session.py`, `meter.py`, `baselines/`, `distill/{__init__,preload,write_runner}.py`.

Dependency rules:
- `corpus/` and `citations/` do not depend on `bundle/`.
- `bundle/*` packages do not depend on each other directly; cross-bundle coordination goes through `api.Bundle`.
- `eval/` and `render/` consume wiki bundles, never modify them.
- `cli/<noun>.py` is a thin adapter — translates argv to one or two domain calls.

---

## Data Layout

```
data/
  corpora/    ingested corpora
  wikis/      wiki bundles
  papers/     input source documents
  test_runs/  test run outputs
```

---

## CLI

Two families. Run under `uv run`. Full grammar in
`.claude/skills/wikify/reference/cli-tool-surface.md`.

**Skill-driven (used by workflow skills):**

```bash
wikify session  init / show / update / checkpoint / close / lock / unlock
wikify kg       seeds / abstracts / evidence
wikify extract  canonicalize
wikify draft    write-request
wikify validate write
wikify bundle   commit-page
wikify meter    record
```

**Deterministic, non-model-calling:**

```bash
wikify ingest        <input> --out <corpus>
wikify refresh       <corpus>
wikify field-detect  <corpus>
wikify trace         <bundle>
wikify sample-claims <bundle>
wikify html          <bundle>
wikify eval          <bundle>
```

---

## Key Vocabulary

| Term | Location | Notes |
|---|---|---|
| `ModelTier` (S / M / L) | `types.py` | Single tier vocabulary; use `tier.value` for strings |
| `Role` | `types.py` | extractor / compactor / editor / writer / orchestrator |
| `BaselineConfig` | `baselines/config.py` | Knobs for the abstract-first baseline |
| `SessionV1` | `session.py` | Durable on-disk session state |
| `CallRecord` / `CostMeter` | `meter.py` | Per-call telemetry + reference aggregator |
| `WriteRequest` / `WriteResponse` | `schema.py` | Frozen Pydantic v2 contracts (`extra="forbid"`) |

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
- The `<quote>` is a verbatim substring of the cited chunk's source text. `wikify validate write` enforces this.

A fabricated quote echoed in the body but absent from the source chunk
fails validation; the page never reaches `pages/`.

---

## Error Handling

- Validation failures (`ValidationError`, `QuoteNotInChunkError`) surface through `wikify validate write` as `validation-<page_id>.json` with `ok=false`. The skill retries once; on second failure escalates per `reference/escalation.md`; on third marks the page `failed`.
- Promotion is gated under the session lock. Lock contention exits 2 (`lock_held`); budget overrun exits 3 (`budget_exceeded`); validation/precondition failure exits 1.
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
