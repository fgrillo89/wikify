# Wikify architecture

## What the system does

Wikify takes a corpus of source documents (PDF, DOCX, PPTX, HTML, MD)
and produces an evidence-grounded wiki bundle: encyclopedic article and
biography pages with citation footnotes that resolve to verbatim
substrings of source chunks. A persistent on-disk session object
coordinates the workflow across subagent and process boundaries.

## Three layers

```
raw files
   │
   ▼  wikify ingest
[ Corpus ]            normalised markdown + chunks + embeddings + KG
   │
   ▼  wikify session/kg/extract/draft/validate/bundle/meter
[ Wiki bundle ]       pages + indices + session + telemetry
   │
   ▼  wikify html / wikify eval
[ Site / metrics ]    static HTML site, M1/M3/M5/M6 reports
```

## Runtime model

The agent runtime — Claude Code or any other agent harness — drives
the workflow. The agent reads skill markdown, calls deterministic CLI
tools via Bash, and spawns model-calling subagents via the Task tool.
Python never calls a model SDK directly.

Three concrete consequences:

- **Skills own the per-iteration loop.** `.claude/skills/wikify/workflows/run-baseline.md` documents the page-by-page loop the agent walks.
- **Files are the agent–backend interface.** Every CLI tool reads inputs from named files (corpus chunks, session.json, scratch artifacts) and writes outputs to named files. The agent passes paths, not blobs.
- **Durable state lives on disk.** A `<bundle>/_session/session.json` carries strategy, budget, stage status, and per-page status across subagent boundaries; the agent can stop and resume without losing place.

## CLI surface

Eight families. The agent learns them from `.claude/skills/wikify/reference/cli-tool-surface.md`.

**Skill-driven (used by workflow skills):**

| Family | Purpose |
|---|---|
| `wikify session` | init / show / update / checkpoint / close / lock / unlock the session |
| `wikify kg` | seeds / abstracts / evidence — corpus knowledge-graph queries |
| `wikify extract` | canonicalize extracted concepts into `session.pages` entries |
| `wikify draft` | build a `WriteRequest` scratch artifact for the writer subagent |
| `wikify validate` | structural + grounding checks on a `WriteResponse` scratch artifact |
| `wikify bundle` | promote a validated response to `pages/<id>.md` and rebuild indices |
| `wikify meter` | record per-call telemetry to `_calls.jsonl` and update budget |

**Deterministic, non-model-calling:**

| Family | Purpose |
|---|---|
| `wikify ingest` | parse + chunk + embed + graph a source directory |
| `wikify refresh` | rebuild derived corpus artifacts |
| `wikify field-detect` | detect the corpus field |
| `wikify trace` | analyse KG exploration traces |
| `wikify sample-claims` | sample factual claims for human evaluation |
| `wikify html` | render a wiki bundle to a static site |
| `wikify eval` | compute M1/M3/M5/M6 metrics |

## Bundle artifact contract

The full file-level contract lives in
`.claude/skills/wikify/reference/schemas.md`. In summary:

```
<bundle>/
├── articles/<id>.md                       canonical article pages
├── people/<id>.md                         canonical biography pages
├── _index.json                            page-level index
├── _index.md                              human-readable index
├── _wiki_graph.json                       cite-edge graph between pages
├── _run.json                              final run snapshot (CostMeter shape)
├── _run_history.jsonl                     append-only per-close history
├── _calls.jsonl                           per-model-call telemetry
├── _session/
│   ├── session.json                       SessionV1 state
│   ├── checkpoints/<label>.json           snapshots
│   └── session.lock                       advisory lock with TTL
├── _scratch/
│   ├── extract-<chunk_id>.json            extract subagent output
│   ├── draft-<page_id>.json               WriteRequest payload
│   ├── response-<page_id>.json            WriteResponse from writer subagent
│   ├── validation-<page_id>.json          validator verdict
│   └── review-<page_id>.json              optional advisory reviews
└── _meta/                                 corpus-relative metadata
```

Every durable artifact carries a `schema_version` envelope. Pydantic
models in `src/wikify/schema.py` and `src/wikify/session.py` are the
executable source of truth.

## Citation grounding

Every committed page enforces:

- `[^eN]` markers in prose resolve 1:1 to `[^eN]:` definitions in a
  `## References` block.
- Each `[^eN]:` definition carries `<chunk_id> (<doc_id>) > "<quote>"`.
- The `<quote>` is a verbatim substring of the cited chunk's source
  text — `wikify validate write` enforces this.

A fabricated quote echoed in the body but absent from the source
chunk fails validation; the page never reaches `pages/`.

## Telemetry contract

The skill path is the only producer of `_run.json` and `_calls.jsonl`.

`_calls.jsonl` carries one `CallRecord` per line:
`role, tier, input_tokens, output_tokens, context_used, context_cap,
wall_seconds, cache_hit, prompt_hash, haiku_eq`.

`_run.json` is the aggregated snapshot at session close, with the
shape `CostMeter.snapshot()` produces: `run_id`,
`budget_used_haiku_eq`, `wall_seconds`, `by_role`, `by_tier`,
`context {used_max, used_mean, headroom_min, headroom_mean}`, `calls`
(integer count), `cache_hit_rate`, plus baseline overlay fields
(`seed_doc_ids`, `seed_chunks_read`, `evidence_chunks_read`,
`split_initial`, `n_pages_written`, etc.).

## Repository layout

```
src/wikify/
├── cli.py                          top-level Typer app
├── cli_cmds/                       skill-driven sub-apps
│   ├── _helpers.py                 shared error / lock helpers
│   ├── session.py
│   ├── kg.py
│   ├── extract.py
│   ├── draft.py
│   ├── validate.py
│   ├── bundle.py
│   └── meter.py
├── session.py                      SessionV1 + lock + run-snapshot writer
├── meter.py                        CallRecord + reference CostMeter
├── schema.py                       canonical Pydantic request/response models
├── paths.py                        bundle / corpus path conventions
├── ingest/                         corpus pipeline (parse, chunk, embed, graph)
├── citestore/                      knowledge-graph fluent API
├── distill/                        seed selection, dossier, prompts, write_runner
├── baselines/                      BaselineConfig + evidence helpers
├── prompts/                        prompt templates loaded by the skill
├── render/                         html site renderer
├── eval/                           metric computations
└── store/                          page / index / vector / wiki-graph persistence
```

## Skill pack

```
.claude/skills/wikify/
├── reference/                      facts and contracts the agent loads
│   ├── schemas.md                  artifact catalog + schema_version policy
│   ├── cli-tool-surface.md         CLI grammar (skill-driven + deterministic)
│   ├── write-constraints.md        Wikipedia-MoS structural rules
│   ├── citation-format.md          [^eN] marker grammar
│   ├── tiers.md                    S/M/L → haiku/sonnet/opus mapping
│   ├── escalation.md               retry-then-tier-L policy
│   ├── atoms.md                    compositional atoms with pre/post-conditions
│   ├── knowledge-graph.md          corpus KG fluent API
│   └── wiki-graph.md               wiki KG fluent API
└── workflows/
    └── run-baseline.md             the baseline workflow loop
```

## Design invariants

- The agent runtime is the only place that calls models.
- Python tools are deterministic, validated, and individually testable.
- Files are the only interface between the agent and the backend.
- No hidden state — every coordination point is a named file.
- Every model-calling step produces a CallRecord in `_calls.jsonl`.
- Page promotion is gated by structural + grounding validation under
  the session lock; lock contention or budget overrun produces
  structured stderr errors with stable exit codes (0 success, 1
  validation/precondition failure, 2 lock_held, 3 budget_exceeded).
