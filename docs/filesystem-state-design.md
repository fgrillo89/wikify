# Filesystem state design

This note records the file contract for skill-driven wikification.

## Goals

- Keep the final wiki separate from the machinery that builds it.
- Make the live state easy for humans and agents to inspect with `ls`,
  `grep`, and `cat`.
- Keep structured machine contracts in JSON / JSONL.
- Keep agent-facing state in compact Markdown with YAML frontmatter.
- Garbage-collect transient writer artifacts after successful commit.
- Let parallel agents suggest globally but mutate only their claimed concept.

## Top-level layout

```text
<bundle>/
  wiki/
    index.md
    articles/
    people/
    data/

  work/
    index.md
    inbox/
      evidence_suggestions.jsonl
      concept_suggestions.jsonl
      merge_suggestions.jsonl
      query_feedback.jsonl
    concepts/
      <slug>/
        work.md
        notebook.md
        evidence.jsonl
        draft.json
        response.json
        validation.json
        .claim

  run/
    state.json
    events.jsonl
    lock

  derived/
    index.json
    navigation.json
    vectors.npz
    stats.jsonl
  claims.db
  wiki.db
```

`wiki/` is the committed human-facing wiki. `work/` is the living build
state. `run/` is execution control and telemetry. `wiki.db` is the committed
wiki query store. `derived/` is rebuildable machine and render output.

## Concept folder contract

Each candidate or committed page has one concept folder:

```text
work/concepts/<slug>/
  work.md
  notebook.md
  evidence.jsonl
  draft.json
  response.json
  validation.json
  .claim
```

`work.md` is the current concept control card. It carries the small mutable
frontmatter header and a compact body, and it replaces separate manifest,
decisions, and open-question files.

`notebook.md` is the separate researcher notebook and provenance file. Its
frontmatter holds the maturity snapshot, the docs/chunks the notebook has
absorbed, the exploration log, and the round history; its body is the freeform
working dossier the explorer/editor maintains.

`.claim` is the per-concept lock file. A worker that wants to mutate a concept
folder must hold its claim; inbox suggestions can still be appended without one.

```markdown
---
page_id: Atomic Layer Deposition
kind: article
status: committed
wiki_path: wiki/articles/atomic-layer-deposition.md
aliases: [ALD]
evidence_chunks: 12
evidence_docs: 7
new_evidence_since_commit: 0
needs_refine: false
last_compacted: 2026-04-25T12:00:00Z
---

# Atomic Layer Deposition

## Working Summary
- Self-limiting vapor-phase film growth technique.

## Coverage
- Covered: definition, mechanism, applications.
- Weak: temperature windows.
- Missing: direct comparison with CVD.

## Decisions
- Keep separate from Chemical Vapor Deposition because ALD has a distinct
  self-limiting cycle mechanism.

## Next Action
Monitor.
```

`evidence.jsonl` is the appendable evidence ledger. It is canonical working
state and may evolve over time.

```jsonl
{"chunk_id":"doc1:003","doc_id":"doc1","quote":"...","score":0.91,"status":"active","used_in_page":true,"note":"definition"}
{"chunk_id":"doc9:004","doc_id":"doc9","quote":"...","score":0.52,"status":"archived","reason":"duplicate"}
```

`draft.json` is an immutable per-attempt writer input snapshot. It is generated
only when a writer or refiner is about to run. It is not appended to and is not
the memory store.

`response.json` is the raw writer/refiner output for the current attempt.

`validation.json` is the promotion gate for the current response.

After a successful commit or refine, `draft.json`, `response.json`, and
`validation.json` are eligible for garbage collection. The long-lived concept
state returns to `work.md` and `evidence.jsonl`.

## Create and refine lifecycle

Create:

```text
work.md + evidence.jsonl
  -> draft.json(task=create)
  -> response.json
  -> validation.json
  -> wiki/articles/<slug>.md or wiki/people/<slug>.md
  -> garbage collect draft/response/validation
  -> work.md status=committed
```

Refine:

```text
cross-talk suggestion
  -> work/inbox/*.jsonl
  -> consolidate inbox
  -> evidence.jsonl updated
  -> work.md status=needs_refine
  -> draft.json(task=refine)
  -> response.json
  -> validation.json
  -> replace committed wiki page
  -> garbage collect draft/response/validation
  -> work.md status=committed
```

For refinement, the draft builder reads the current committed wiki page, the
current `work.md`, and active evidence from `evidence.jsonl`, then writes a
fresh `draft.json`. The writer/refiner reads the fresh draft, not the loose
source files.

## Refinement triggers

A deterministic tending pass may mark a committed concept `needs_refine` when:

- `new_active_evidence_since_commit >= 3`
- `new_evidence_docs_since_commit >= 2`
- new evidence is tagged as a contradiction
- new evidence fills a listed weak or missing coverage area
- a merge or split decision affects the committed page
- the user explicitly requests refinement

## Cross-talk

Workers may suggest globally but may only mutate their claimed concept.
Cross-talk is append-only:

```text
work/inbox/evidence_suggestions.jsonl
work/inbox/concept_suggestions.jsonl
work/inbox/merge_suggestions.jsonl
work/inbox/query_feedback.jsonl
```

The consolidation pass owns applying suggestions:

```text
1. Read inbox records.
2. Validate schema and quote grounding.
3. Deduplicate by target concept and chunk id, or by normalized title.
4. Resolve the target as existing concept, new concept, or merge candidate.
5. Append accepted evidence to the target `evidence.jsonl`.
6. Update the target `work.md` status.
7. Mark committed pages `needs_refine` when thresholds are met.
8. Regenerate `work/index.md`.
9. Garbage-collect processed/rejected inbox records after summarizing counts.
```

## Tending and compaction

`work/index.md` is generated from concept frontmatter and validation state. It
is a dashboard, not canonical memory.

A deterministic tending command should:

- regenerate `work/index.md`
- deduplicate and rank `evidence.jsonl`
- validate evidence quotes still resolve to source chunks
- mark concepts `ready`, `needs_refine`, or `committed`
- compact oversized `work.md` files back to the fixed section contract
- expire stale claims
- consolidate inbox suggestions
- regenerate `wiki/index.md` and `derived/index.json` when needed

The rule is: committed wiki pages may grow richly; work files stay compact and
operational.

## Monitoring surface

The run surface should stay smaller than the work surface. It only needs to
answer five questions:

- Can the run resume?
- Who owns mutation right now?
- What happened, in order?
- What did model calls cost?
- What was the final run outcome?
- How can runs be replayed and compared?

Minimal default:

```text
run/
  state.json
  events.jsonl
  lock
```

`state.json` is the only mutable run-control file. It should stay small:
identity, strategy, corpus/wiki/work paths, budget target, coarse stage
status, and run status. Concept memory belongs in `work/concepts/*/work.md`,
not in run state. Spend is never stored in state; it is derived on demand from
`events.jsonl` (sum over `type == "call"`), so it can never drift from the
recorded calls.

```json
{
  "schema_version": 1,
  "run_id": "baseline-001",
  "status": "active",
  "strategy": "baseline",
  "corpus_path": "data/corpora/foo",
  "corpus_fingerprint": "1a2b3c4d5e6f7890",
  "wiki_path": "wiki",
  "work_path": "work",
  "budget": {
    "target_haiku_eq": 500000
  },
  "stages": {
    "extract": "done",
    "write": "running"
  },
  "created_at": "2026-04-25T10:00:00Z",
  "updated_at": "2026-04-25T10:30:00Z"
}
```

`events.jsonl` is the single structured run log and telemetry ledger. It
replaces separate calls, history, and default checkpoint files. Cost is computed
by filtering `type == "call"`. Exploration replay is computed by reading events
in order.

`lock` is ephemeral coordination state. It can be JSON internally, but it is not
metrics or history.

There is no default `summary.json`. Summaries are generated on demand from
`state.json`, `events.jsonl`, `wiki/`, and `work/`. If eval or publication needs
a frozen summary, write it as a derived/export artifact, not as core run state.

Files that should not be treated as monitoring:

- `validation.json` is a page-attempt gate.
- `draft.json` is a per-attempt writer input snapshot.
- `response.json` is a per-attempt writer output.
- `derived/index.json`, `derived/navigation.json`, `derived/vectors.npz`, and `wiki.db` are rebuildable projections.
- processed inbox records are garbage-collected after consolidation.

## Event log telemetry

`events.jsonl` is a structured log: one JSON object per line, append-only. It
preserves facts that happened over time without storing full chunks, prompts, or
page bodies.

Common envelope:

```json
{
  "schema_version": 1,
  "event_id": "01H...",
  "run_id": "baseline-001",
  "type": "chunk_read",
  "at": "2026-04-25T10:00:00Z",
  "actor": "explorer-1",
  "concept_id": "Atomic Layer Deposition",
  "page_id": "Atomic Layer Deposition",
  "chunk_id": "doc1:003",
  "doc_id": "doc1",
  "stage": "write",
  "data": {}
}
```

Required fields:

- `schema_version`
- `event_id`
- `run_id`
- `type`
- `at`
- `actor`
- `data`

Optional top-level indexing fields make grep/filtering cheap:

- `concept_id`
- `page_id`
- `chunk_id`
- `doc_id`
- `stage`

Allowed event vocabulary (the literal `EventType` union; appending an unknown
type raises a validation error):

- `stage_changed`
- `cli_invoked`
- `concept_created`
- `concept_status_changed`
- `chunk_read`
- `evidence_added`
- `inbox_suggestion_created`
- `inbox_consolidated`
- `query_started`
- `wiki_page_read`
- `query_feedback_created`
- `draft_created`
- `call`
- `validation_completed`
- `page_committed`
- `page_refined`
- `budget_exceeded`
- `run_closed`
- `round_started`
- `round_completed`
- `dossier_promoted`
- `dossier_stalled`
- `dossier_parked`
- `pattern_dispatched`
- `corpus_drift_detected`
- `page_embedding_failed`
- `data_page_collision_skipped`
- `page_recall_cleared`

Examples:

```jsonl
{"schema_version":1,"event_id":"e0","run_id":"baseline-001","type":"cli_invoked","at":"2026-04-25T10:00:30Z","actor":"agent","stage":"explore","data":{"argv":["wikify","corpus","find","Atomic Layer Deposition","--top-k","3"],"cwd":"data/wikis/baseline-001","exit_code":0,"stdout_path":"run/io/e0.stdout.txt","stderr_path":"run/io/e0.stderr.txt","stdout_preview":"0.84  doc1:003  doc1  Atomic layer deposition...","stderr_preview":""}}
{"schema_version":1,"event_id":"e1","run_id":"baseline-001","type":"chunk_read","at":"2026-04-25T10:01:00Z","actor":"explorer-1","concept_id":"Atomic Layer Deposition","chunk_id":"doc1:003","doc_id":"doc1","data":{"reason":"evidence_retrieval","source":"kg evidence","score":0.91}}
{"schema_version":1,"event_id":"e2","run_id":"baseline-001","type":"evidence_added","at":"2026-04-25T10:02:00Z","actor":"consolidator","concept_id":"Atomic Layer Deposition","chunk_id":"doc1:003","doc_id":"doc1","data":{"evidence_path":"work/concepts/atomic-layer-deposition/evidence.jsonl","quote_hash":"abc123"}}
{"schema_version":1,"event_id":"e3","run_id":"baseline-001","type":"call","at":"2026-04-25T10:03:00Z","actor":"writer-2","page_id":"Atomic Layer Deposition","stage":"write","data":{"role":"writer","tier":"M","input_tokens":12000,"output_tokens":3000,"haiku_eq":240.0,"prompt_hash":"def456"}}
{"schema_version":1,"event_id":"e4","run_id":"baseline-001","type":"page_committed","at":"2026-04-25T10:05:00Z","actor":"commit","page_id":"Atomic Layer Deposition","data":{"path":"wiki/articles/atomic-layer-deposition.md","evidence_chunks":8,"evidence_docs":5}}
```

Replay tools should reconstruct:

- CLI commands issued and the exact text shown to the model
- docs and chunks read over time
- concepts discovered
- evidence accumulated
- writer/refiner calls and costs
- validation failures
- pages committed and refined
- budget curve over time

Every action that changes wiki-building state should emit an event. If a fact
is required for replay or cost comparison, it belongs in `events.jsonl`. If it
is rich working memory, it belongs in `work/`, not in the run log.

### CLI IO telemetry

Because the default agent interface is terse text stdout, replay needs to know
what the model actually saw. Each tool invocation can therefore emit a
`cli_invoked` event.

The event records compact metadata inline:

```text
argv
cwd
exit_code
duration_ms
stdout_preview
stderr_preview
stdout_path
stderr_path
```

Full stdout and stderr should be written under a transient run IO directory when
capture is enabled:

```text
run/
  events.jsonl
  io/
    <event_id>.stdout.txt
    <event_id>.stderr.txt
```

This keeps `events.jsonl` token-light while still allowing exact replay of the
agent-visible command history. Small outputs may fit entirely in the preview;
large outputs are preserved by path.

Rules:

- Capture CLI IO by default for skill-driven runs.
- The CLI is the default way for agents to interact with bundle state.
- Do not treat `run/io/` as working memory; it is replay/debug telemetry.
- Redact known secrets from argv/stdout/stderr before writing telemetry.
- Prefer previews in `events.jsonl`; store full text in `run/io/`.
- Do not duplicate durable artifacts into IO logs when a command already wrote
  an output file; log the path instead.
- Garbage collection may compact old IO logs after exporting a replay bundle,
  but should not remove IO for active or recently completed runs by default.

### CLI as the bundle interface

Agents should not normally read bundle JSON files directly. JSON/JSONL files are
the durable storage contract; CLI commands are the agent-facing parsing layer.

Default rule:

```text
agent reads bundle state through CLI text views
agent mutates bundle state through CLI commands
python reads/writes JSON/YAML/JSONL under schemas
```

This gives the model token-light, task-shaped text instead of raw schema
payloads:

```text
wikify work show atomic-layer-deposition
wikify wiki show "Atomic Layer Deposition"
wikify run show
wikify corpus show chunk:doc1:003
```

The CLI should provide JSON-to-text/YAML renderers for every durable structured
file an agent may need to inspect:

```text
run/state.json                         -> wikify run show
run/events.jsonl                       -> wikify run list events
work/concepts/<slug>/evidence.jsonl    -> wikify work list evidence <slug>
work/inbox/*.jsonl                     -> wikify work list inbox
work/concepts/<slug>/draft.json        -> wikify draft show <slug>
work/concepts/<slug>/validation.json   -> wikify draft check <slug>
derived/index.json                     -> wikify wiki list/find/show
wiki.db                                -> wikify wiki find/traverse
derived/navigation.json                -> wikify render, category fallback
```

Decision: wrap basic file exploration inside the CLI. Skill instructions should
tell agents to use these CLI commands for normal work instead of raw `ls`, `rg`,
or `cat`. Direct shell file tools are reserved for debugging, tests, and
repository development, not for the standard wikification workflow.

The CLI must therefore provide first-class equivalents of `ls`, `grep`, and
`cat` for every bundle area the agent needs to inspect:

```text
list  -> ls
find --text  -> grep / rg
show  -> cat
```

Examples:

```text
wikify wiki list files
wikify wiki find "atomic layer deposition" --text
wikify wiki show wiki/articles/atomic-layer-deposition.md --full

wikify work list claims
wikify work list inbox
wikify work show atomic-layer-deposition
```

This keeps the model's familiar shell workflow:

```text
list possible files/pages
grep for a phrase
cat/show the selected result
```

but routes it through a controlled bundle interface that can:

- restrict reads to the active bundle/corpus roots
- hide raw JSON behind text/YAML renderers
- cap output by default
- log exactly what the model saw
- return semantic handles when possible

Everything the agent may need to inspect during a run should have a terse text
view and an explicit `--format json` escape hatch. If the agent needs full page
prose or a full concept card, it should still request it through `show --full`
so the access is logged.

## Query-driven improvement

Query is not only a read path. It can be a feedback loop that continuously
improves the wiki.

The query agent's default responsibility:

```text
1. Answer from the committed wiki when possible.
2. Fall back to corpus retrieval when the wiki is insufficient.
3. Emit structured feedback when the answer exposed a wiki gap.
```

The query agent may read:

```text
wiki/index.md
wiki/articles/*.md
wiki/people/*.md
derived/index.json
corpus chunks via deterministic tools
```

The query agent may write only:

```text
work/inbox/query_feedback.jsonl
run/events.jsonl
```

It must not directly edit concept work files, evidence ledgers, or committed
wiki pages. The consolidator applies query feedback through the normal
create/refine lifecycle.

Example query feedback:

```jsonl
{"query":"How does ALD differ from CVD?","answer_source":"wiki+corpus","affected_pages":["Atomic Layer Deposition"],"missing_concepts":["Chemical Vapor Deposition"],"gap":"ALD page lacks direct comparison with CVD.","evidence_chunks":["doc4:011","doc7:002"],"severity":"medium"}
```

Consolidation may convert query feedback into:

- new concepts
- new evidence for existing concepts
- `needs_refine` status on committed pages
- merge/split suggestions
- priority changes in `work.md`

Query-driven wikification is a workflow mode over the same bundle structure:

```text
query
  -> read wiki/index.md and relevant pages
  -> retrieve corpus chunks if needed
  -> answer user
  -> write query feedback
  -> consolidate inbox
  -> create/refine concept folders
  -> draft/response/validation
  -> commit
```

Two useful variants:

- Reactive query mode: the wiki evolves only from real user questions.
- Scripted query mode: the skill generates a curriculum of questions from seed
  chunks, uncovered corpus regions, or coverage residuals.

This mode should still use the same promotion path. Query agents suggest;
builder/refiner agents commit.

## Python surface and ownership

Python should expose deterministic bundle operations. Skills should own strategy
policy, loop shape, agent spawning, and stopping criteria.

The boundary:

```text
skills decide what to do next
python validates and mutates named bundle files
models only communicate through files
```

Do not add Python entry points named after strategies. Strategy lives in
skills; Python exposes atoms (CLI verbs and the corresponding fluent API)
that any skill can compose.

### Bundle component owners

`BundlePaths` owns path conventions only. It should not contain workflow logic.

`RunStore` owns:

- `run/state.json`
- `run/events.jsonl`
- `run/lock`

Responsibilities:

- initialize and close runs
- acquire/release locks
- append typed events
- update small mutable run state
- compute token/cost aggregates from `events.jsonl`

`CorpusBuilder` owns corpus creation and refresh:

- parse source documents
- chunk text
- extract images/equations/citations
- build embeddings and graph artifacts
- validate the produced corpus contract

It is deterministic and can be driven by an ingest skill. It does not mutate a
wiki bundle.

`CorpusStore` owns read-only corpus access during wikification:

- list documents and chunks
- load chunks by id
- retrieve/search evidence
- expose graph neighborhoods
- expose document sampling primitives

It must not mutate wiki or work state. In a bundle run, corpus commands are
read-only unless explicitly running the outer `corpus build` / `corpus refresh`
pipeline outside the bundle.

`WorkStore` owns live wikification state:

- `work/index.md`
- `work/inbox/*.jsonl`
- `work/concepts/<slug>/work.md`
- `work/concepts/<slug>/evidence.jsonl`

Responsibilities:

- create concepts
- update concept status/frontmatter
- append and deduplicate evidence
- append inbox suggestions
- consolidate inbox suggestions
- compact concept work files
- regenerate `work/index.md`
- decide deterministic readiness/refinement flags

`DraftBuilder` owns per-attempt writer input:

- `work/concepts/<slug>/draft.json`

Responsibilities:

- compile create/refine drafts from `work.md`, `evidence.jsonl`, corpus chunks,
  current wiki page when refining, and style constraints
- never treat drafts as persistent memory
- emit `draft_created` events

`Validator` owns validation:

- `work/concepts/<slug>/validation.json`

Responsibilities:

- validate writer/refiner responses against schemas
- validate citation markers and quote grounding
- emit `validation_completed` events

`WikiStore` owns committed wiki output:

- `wiki/index.md`
- `wiki/articles/*.md`
- `wiki/people/*.md`

Responsibilities:

- promote validated responses
- replace committed pages during refinement
- regenerate token-light `wiki/index.md`
- emit `page_committed` and `page_refined` events

`DerivedStore` owns rebuildable machine projections:

- `derived/index.json`
- `derived/navigation.json`
- `derived/vectors.npz` (page ids are stored inside the npz, not a sidecar)
- `wiki.db` (the committed-wiki query + graph store)

Responsibilities:

- rebuild projections from committed wiki pages and evidence
- never act as canonical state

### Atomic actions

The Python CLI should expose a small Unix-like tool language. The default agent
loop should feel like:

```text
list  -> ls
find  -> grep / rg, but semantic and graph-aware
show  -> cat, but parsed and token-aware
add   -> append controlled state
set   -> update small controlled state
build -> compile a generated artifact
check -> validate readiness or correctness
commit/tend/close -> lifecycle gates
```

Prefer positional handles and queries where they are unambiguous. Use flags only
to disambiguate or constrain scope.

Run commands:

```text
wikify run init   --bundle <bundle> --corpus <corpus> [--strategy <name>] [--target-haiku-eq N]
wikify run show   [--detail|--full] [--format text|json]
wikify run list   events [--tail N] [--type <type>]
wikify run set    [--target-haiku-eq N] [--strategy-note <text>]
wikify run lock   [--owner <id>] [--ttl-seconds N]
wikify run unlock
wikify run close  [--status completed|failed|abandoned]
```

The corpus path is fixed at `run init` and recorded in `run/state.json`;
`run set` cannot change it. Open a fresh bundle if the corpus changes.

Corpus build/read commands (every flag below is on the actual CLI; consult
`--help` per subcommand for the authoritative set):

```text
wikify corpus build    <source> --out <corpus> [--mode additive|sync] [--parser default|lite|docling]
wikify corpus refresh  <corpus> [--no-openalex]
wikify corpus schema   [--format text|json]
wikify corpus check    [<corpus>] [--full] [--format text|json]
wikify corpus list     docs|chunks|files [--corpus <c>] [--doc <doc>] [--long]
wikify corpus find     "<query>" [--corpus <c>] [--top-k N] \
                       [--by chunk|paper|author] \
                       [--rank semantic|bm25|hybrid|all|citation_count|pagerank|h_index|n_papers] \
                       [--field chunk_text|title] [--in-doc <doc>] [--exclude-kind <kind>] \
                       [--with-text] [--format auto|quiet|compact|json] [--explain]
wikify corpus find     "<query>" [--corpus <c>] --text
wikify corpus sample   [--corpus <c>] [--max N] [--strategy diverse] [--pagerank-weight W]
wikify corpus show     <doc:|chunk:|figure:|equation:|author:><id> [--corpus <c>] [--full] [--long]
wikify corpus traverse <handle> --to <relation> [--corpus <c>] \
                       [--rank citation_count|pagerank] [--top-k N] \
                       [--format auto|quiet|compact|json] [--explain]
wikify corpus repl     [--corpus <c>]
```

`corpus build` is the skill-facing ingest pipeline.

`--corpus` is optional everywhere a read command accepts it.
Resolution order: explicit flag → `WIKIFY_CORPUS` env → walk up from
cwd looking for a directory with `manifest.json` and `docs/`.

Run `wikify corpus schema` to self-discover the available node types,
edge kinds, traverse relations, and rank metrics. `--explain` on
`find`/`traverse` prints the resolved fluent-chain pseudocode without
executing.

Traverse relations:

- `doc:` → `cited-by | references | chunks | figures | equations | authors`
- `chunk:` → `source | cited-in-corpus | figures | equations`
- `author:` → `sources | coauthors`

Output is handles by default when stdout is piped, so multi-hop
queries compose with shell pipes (no special multi-hop CLI syntax
required).

Inside `corpus repl`, `find` returns chunks and `find-papers` returns
papers ranked by their best matching chunks.

Work commands:

```text
wikify work list claims|inbox
wikify work list evidence <concept>
wikify work show <concept> [--full]
wikify work add concept "Atomic Layer Deposition" --kind article [--aliases '[...]']
wikify work add evidence <concept> --records <jsonl-path>
wikify work add feedback evidence|concept|merge|query --record <json-or-path>
wikify work set <concept> [--status <s>] [--needs-refine] [--aliases '[...]']
wikify work claim <concept> [--owner <id>] [--ttl-seconds N]
wikify work release <concept> [--owner <id>]
wikify work tend [--keep-inbox]
wikify work build-evidence <concept> ...
wikify work cluster-concepts ...
```

Draft commands:

```text
wikify draft build <concept> --task create|refine --corpus <c> --model-id <id> --tier S|M|L [--with-adjacent]
wikify draft show <concept> [--full]
wikify draft check <concept> [--dry-run]
wikify draft render-dossier <concept>
wikify draft normalize-references <concept>
wikify draft finalize <concept>
```

Wiki commands:

```text
wikify wiki list articles|people|files
wikify wiki find "ALD temperature window" [--mode text|bm25|semantic|hybrid] [--top-k N]
wikify wiki traverse "Atomic Layer Deposition" --to links
wikify wiki traverse "Memristor" --to linked-by
wikify wiki traverse "Atomic Layer Deposition" --to co-evidence
wikify wiki traverse "Atomic Layer Deposition" --to evidence
wikify wiki traverse "Atomic Layer Deposition" --to similar
wikify wiki traverse "Atomic Layer Deposition" --to see-also
wikify wiki traverse "Atomic Layer Deposition" --to categories
wikify wiki traverse category:materials --to pages|children|parent
wikify wiki show "Atomic Layer Deposition" [--full]
wikify wiki schema [--format text|json]
wikify wiki repl
wikify wiki build indexes|graph|vectors
wikify wiki rebuild [--skip vectors|indexes|graph]
wikify wiki relink [--max-links N] [--min-overlap N] [--dry-run]
wikify wiki navigation-context [--run <bundle>] [--out <path>]
wikify wiki apply-navigation <path> [--run <bundle>]
wikify wiki check
wikify wiki commit <concept> [--ensure-projections]
```

`wiki navigation-context` is the first-class organizer query projection. It
writes compact page metadata, deterministic cluster hints from links, shared
evidence documents, and title/excerpt token overlap, the existing navigation
tree when present, and freshness deltas for new or changed page ids.
`wiki apply-navigation` validates and persists `derived/navigation.json`.
The render contract remains `groups` with recursive `children` and `page_ids`;
additional metadata is for organizer agents and freshness checks.

Rendering and eval remain downstream deterministic tools. They consume a wiki
bundle and should never mutate corpus/work state. They use `--bundle <b>`
since they never resolve through `run/state.json`:

```text
wikify render --bundle <bundle> [--format html] [--out <dir>] [--corpus <corpus>] [--wiki-name <name>]
wikify eval --bundle <bundle> [--corpus <corpus>] [--report <path>]
```

`render` writes per-page HTML plus `references.html` and `graph.html` site
outputs from the committed wiki.

### Design rules for atoms

- Mutating atoms require an active run context, resolved from `--run` or
  `./run/state.json`.
- Read commands emit terse text by default.
- Use `--format json` only for scripts, tests, or automation that needs stable
  parsing.
- Use `--detail` for compact operational metadata.
- Use `--full` only when the agent explicitly needs heavy content.
- Commands that would emit large payloads write a file and return its path.
- Every mutating atom appends an event to `run/events.jsonl`.
- Every mutating atom acquires `run/lock`.
- Atoms are idempotent where possible: deduplicate evidence by `chunk_id`, avoid
  duplicate concepts by normalized title, and make index rebuilds pure.
- Skills may read Markdown directly, but durable structured mutations should go
  through atoms.
- Python validates schemas, paths, and quote grounding. Skills decide policy.

### Skill composition examples

Baseline:

```text
corpus sample
  -> extractor agents write extract outputs
  -> work add concept
  -> corpus find "<concept>"
  -> work add evidence
  -> work tend
  -> draft build
  -> writer agent
  -> draft check
  -> wiki commit
```

Guided exploration:

```text
read work/index.md
  -> corpus list/find/show recursively
  -> append evidence or inbox suggestions
  -> work tend
  -> write/refine ready concepts
```

Query-driven:

```text
read wiki/index.md and pages
  -> wiki find/show
  -> corpus find/show if answer needs fallback
  -> answer user
  -> work add feedback query
  -> work tend
  -> create/refine pages through the normal path
```

The important property is that adding a new strategy should require a new skill
recipe, not a new Python workflow.

## Homogeneous tool API

The Python interface should be a small tool language that is easy to teach to an
agent. Prefer a stable grammar over many bespoke commands.

Distilled top-level nouns:

```text
run
corpus
work
draft
wiki
data
```

`corpus` covers both corpus creation (`build` / `refresh`) and read-only corpus
exploration (`list` / `find` / `show` / `check`). During an active wiki run,
corpus exploration is read-only.

`data` owns the data-wave numeric claim store (`claims.db`) and its evolving
`kind=data` artifact tables: `add` / `list` / `show` / `query` / `coverage` /
`consolidate` / `commit` / `rebuild` / `list-artifacts`.

Everything else is a sub-kind, positional handle, or option:

```text
concepts, evidence, inbox, validation, indexes, graph, vectors
```

Minimal verb vocabulary:

```text
init
show
list
find
add
set
build
check
commit
tend
close
```

Meanings:

- `show`: inspect one thing.
- `list`: inspect many handles.
- `find`: retrieve candidates from corpus or wiki.
- `add`: create or append durable work records.
- `set`: update small state fields.
- `build`: compile a generated artifact.
- `check`: validate an artifact.
- `commit`: promote validated work into the wiki.
- `tend`: consolidate, compact, deduplicate, garbage-collect, and regenerate
  dashboards.

The CLI should mirror this grammar:

```text
wikify <noun> <verb> ...
```

Prefer the shortest readable form:

```text
wikify <noun> list [kind]
wikify <noun> find [query] [scope flags]
wikify <noun> show <handle>
wikify <noun> add <kind> ...
wikify <noun> set <handle> ...
```

By default, commands resolve the active run from `./run/state.json` when the
current working directory is a bundle root. That run state supplies the default
corpus path, bundle paths, telemetry target, and budget context. This keeps the
common skill path token-light:

```text
cd <bundle>
wikify corpus find "Atomic Layer Deposition" --top-k 8
```

Resolution order:

```text
1. explicit --run <bundle>/run/state.json
2. ./run/state.json from the current directory
3. explicit --corpus <corpus> for standalone read-only corpus inspection
4. clear error
```

Use `--run` for cross-bundle operations and replay/debugging. Use `--corpus`
only for standalone corpus inspection outside a wikification run. The corpus
path is set once at `run init` and recorded in `run/state.json`; it is not
mutable through `run set`. If the corpus must change, open a fresh bundle.

Examples (every line below mirrors the actual CLI; consult `--help` for the
authoritative flag set on each subcommand):

```text
wikify run init --bundle <bundle> --corpus <corpus> [--strategy baseline]
wikify run show
wikify run set  --target-haiku-eq 50000
wikify run close --status completed

wikify corpus build papers/ald --out data/corpora/ald [--mode additive|sync]
wikify corpus refresh data/corpora/ald
wikify corpus check   data/corpora/ald
wikify corpus list    docs --corpus data/corpora/ald
wikify corpus sample  --corpus data/corpora/ald --max 20 --pagerank-weight 0.7
wikify corpus find    "Atomic Layer Deposition" --corpus data/corpora/ald --top-k 8
wikify corpus find    "Atomic Layer Deposition" --corpus data/corpora/ald --text
wikify corpus show    chunk:doc1__c003 --corpus data/corpora/ald --full

wikify work list claims
wikify work show "Atomic Layer Deposition"
wikify work add concept  "Atomic Layer Deposition" --kind article --aliases '["ALD"]'
wikify work add evidence "Atomic Layer Deposition" --records evidence.jsonl
wikify work add feedback query --record feedback.json
wikify work set "Atomic Layer Deposition" --status needs_refine
wikify work claim   "Atomic Layer Deposition" --ttl-seconds 1800
wikify work release "Atomic Layer Deposition"
wikify work tend

wikify draft build "Atomic Layer Deposition" --task create --corpus data/corpora/ald --model-id claude-sonnet-4-6 --tier M
wikify draft show  "Atomic Layer Deposition" --full
wikify draft check "Atomic Layer Deposition"

wikify wiki commit "Atomic Layer Deposition"
wikify wiki show   "Atomic Layer Deposition" --full
wikify wiki find   "ALD vs CVD" --text
wikify wiki build  indexes
wikify wiki check

wikify render --bundle <bundle> --format html [--out <dir>] [--corpus <corpus>]
wikify eval   --bundle <bundle> [--corpus <corpus>] [--report <path>]
```

```python
from wikify.api import Bundle

bundle = Bundle.open("data/wikis/run-001")

bundle.run.init(corpus="data/corpora/foo", strategy="baseline")
bundle.work.add("concept", "Atomic Layer Deposition", kind="article")
records = bundle.corpus.find("Atomic Layer Deposition", top_k=8)
bundle.work.add(kind="evidence", concept="atomic-layer-deposition", records=records)
bundle.work.tend()
bundle.draft.build("atomic-layer-deposition", task="create")
bundle.draft.check("atomic-layer-deposition")
bundle.wiki.commit("atomic-layer-deposition")
bundle.wiki.build(kind="indexes")
bundle.run.close(status="completed")
```

This still allows a richer internal fluent graph API, but that richness should
not leak into the agent-facing command grammar. For example:

```python
bundle.corpus.graph.chunk("doc1:003").neighbors(depth=2).rank("pagerank").top(20)
```

should be exposed to the agent as a simple query atom. The current
agent-facing surface includes `corpus find`, `corpus sample`, `corpus
traverse`, `corpus show`, and `corpus schema`; recursive graph work
composes those primitives with the fluent corpus KG concepts exposed
by `corpus schema`.

### Corpus query shapes

The existing corpus `KnowledgeGraph` fluent API supports source, author, chunk,
section, figure, equation, citation, neighborhood, metric, and vector-search
queries. The agent-facing CLI should not expose each fluent method as a new
verb. It should expose a shell-like read surface plus build/refresh:

```text
corpus build
corpus refresh
corpus show
corpus list
corpus find
corpus sample
corpus traverse
corpus schema
corpus check
corpus repl
```

`corpus build` is ingest:

```text
wikify corpus build papers/ald --out data/corpora/ald
wikify corpus build papers/ald --out data/corpora/ald --parser lite
wikify corpus refresh data/corpora/ald
wikify corpus check data/corpora/ald
```

The ingest skill should use these commands and treat the resulting corpus as a
separate artifact. It can then start a wiki run with:

```text
wikify run init --bundle data/wikis/ald --corpus data/corpora/ald
```

`corpus show` dereferences one known handle:

```text
wikify corpus show doc:paper_A
wikify corpus show chunk:paper_A__c0003__a1b2 --full
wikify corpus show author:smith_j
wikify corpus show figure:paper_A/fig_01
wikify corpus show equation:paper_A_eq1
```

`corpus list` enumerates handles without content:

```text
wikify corpus list docs
wikify corpus list chunks --doc paper_A
wikify corpus list files
```

`corpus find` exposes two retrieval modes: semantic evidence
search (default) and literal substring grep (`--text`). Query-free
diverse-document sampling lives in its own verb, `corpus sample`.

```text
wikify corpus find   "Atomic Layer Deposition" --corpus <corpus> --top-k 8
wikify corpus find   "atomic layer deposition" --corpus <corpus> --text
wikify corpus sample --corpus <corpus> --max 20 --strategy diverse --pagerank-weight 0.7
```

Graph-shaped retrievals (cited-by, near-chunk, neighbours, figures,
equations, authored-by) are exposed through one-hop `corpus traverse`
relations. Run `wikify corpus schema` for the current relation set and
compose recursive traversals by piping handles between calls.

This maps cleanly to fluent calls:

```text
kg.source("Y").cited_by().chunks().search("concept X", top_k=5)
kg.source("X").cited_by().sections(type="conclusions").chunks().collect()
kg.source("X").cited_by().figures().collect()
```

The preferred power mechanism is recursive CLI traversal: inspect small outputs,
choose a handle, then issue the next command. This mimics `ls` / `grep` / `cat`
and gives the LLM more control than a large graph DSL.

```text
1. corpus find "concept A" --corpus <c>
2. choose a chunk handle from the ranked results
3. corpus show chunk:<id> --corpus <c> --full
4. corpus traverse doc:<id> --to cited-by --corpus <c>
5. corpus find "concept B" --corpus <c> --top-k 8
6. corpus show chunk:<best-chunk> --corpus <c> --full
```

Cross-document graph traversals (cited-by, references, near-chunk
figures/equations, authored-by, coauthors) are exposed via
`corpus traverse <handle> --to <relation>`. Multi-hop scoping is
performed by chaining traversals through shell pipes; each step
prints handles that feed directly into the next call. For arbitrary
graph-program composition use the Python fluent API in a deterministic
helper script.

Like `wiki find --text`, `corpus find <query> --text` is the corpus-aware grep
path. It does literal substring matching over chunk text and returns chunk +
doc handles with a short snippet. Pair with `--field title` (and `--by paper`)
for title-only substring search. It is useful when the agent is looking for an
exact phrase, acronym, equation label, or section heading and semantic search
would be wasteful.

If a strategy needs arbitrary graph-program composition, it should use the
Python fluent API inside a deterministic helper or skill-owned script and write
the result to a JSONL file. The general CLI should cover common retrieval
shapes, not become a graph query language.

### Wiki query shapes

The wiki also has a fluent graph API. It is smaller than the corpus graph and
should be exposed through the existing `wiki` noun rather than a new top-level
`graph` noun.

The wiki graph contains:

```text
page      committed wiki page
evidence  citation/evidence node attached to a page
```

The important traversals are:

```text
links        pages this page links to
linked-by    pages that link to this page
co-evidence  pages sharing corpus evidence documents
evidence     evidence entries attached to pages
search       vector search over committed page bodies
```

Use the same read verbs:

```text
wiki show
wiki list
wiki find
wiki check
```

`wiki show` dereferences one committed page handle:

```text
wikify wiki show "Atomic Layer Deposition"
wikify wiki show page:atomic-layer-deposition
wikify wiki show wiki/articles/atomic-layer-deposition.md
wikify wiki show "Atomic Layer Deposition" --full
```

Per-page evidence entries are read via
`wiki traverse "<page>" --to evidence`, which emits `chunk:<id>`
handles that pipe into `corpus show` / `corpus traverse`.

`wiki list` enumerates committed wiki handles:

```text
wikify wiki list
wikify wiki list articles
wikify wiki list people
wikify wiki list files
```

`wiki find` exposes graph and search patterns:

```text
wikify wiki find "resistive switching mechanism" --top-k 5
wikify wiki find "atomic layer deposition" --mode text
wikify wiki find "hafnium oxide switching" --mode semantic
wikify wiki traverse "Atomic Layer Deposition" --to links
wikify wiki traverse "Memristor" --to linked-by
wikify wiki traverse "Atomic Layer Deposition" --to co-evidence
wikify wiki traverse "Atomic Layer Deposition" --to evidence
wikify wiki traverse "Atomic Layer Deposition" --to similar --top-k 5
wikify wiki traverse "Atomic Layer Deposition" --to categories
wikify wiki traverse category:materials --to pages
```

This maps to the fluent API:

```text
wkg.search("resistive switching mechanism", top_k=5)
wkg.page("Atomic Layer Deposition").links().collect()
wkg.page("Memristor").linked_by().collect()
wkg.page("Atomic Layer Deposition").co_evidence().collect()
wkg.page("Atomic Layer Deposition").evidence().collect()
```

`wiki find <query> --mode text` is the bundle-aware grep path. It searches
committed Markdown bodies and returns compact matches with page path and a short
snippet:

```text
wiki/articles/atomic-layer-deposition.md:1  Atomic Layer Deposition
wiki/articles/atomic-layer-deposition.md:14 atomic layer deposition (ALD) is...
```

Without `--mode`, `wiki find <query>` uses hybrid search:

```text
1. wiki.db BM25 over committed page title/body
2. page-vector semantic search when wiki embeddings exist
3. reciprocal-rank fusion over the available result lists
```

If neither wiki.db nor page vectors are available, hybrid mode falls back to the
text path.

Cross-graph workflows should stay explicit. The CLI should not hide whether the
agent is reading the committed wiki or the underlying corpus:

```text
wikify wiki find "hafnium oxide switching" --top-k 3
wikify corpus find "hafnium oxide switching" --top-k 10
wikify corpus find "temperature window" --in-doc doc:hafnium_oxide_review
```

The wiki graph is used for lifecycle decisions:

```text
covered?       wiki search finds a strong existing page
thin?          page has too little evidence
orphan?        page has no links, backlinks, or co-evidence
overlap?       page is highly similar to another page
refine target? page has new corpus evidence or query feedback
```

Like corpus commands, wiki read commands return compact handles by default and
require `--full` for page bodies or long evidence quotes.

### Primitive artifact model

Skills compose a few primitive artifact types:

```text
Handle        token-light reference to a file, concept, chunk, page, or event
Record        one JSON object with schema_version
Ledger        JSONL append-only record stream
ControlCard   Markdown file with YAML frontmatter and fixed sections
Projection    generated artifact derived from canonical state
```

Mapping:

```text
run/state.json                         Control record
run/events.jsonl                       Ledger
work/concepts/<slug>/work.md           ControlCard
work/concepts/<slug>/notebook.md       ControlCard, researcher notebook
work/concepts/<slug>/evidence.jsonl    Ledger
work/inbox/*.jsonl                     Ledger
work/concepts/<slug>/draft.json        Record, per-attempt
work/concepts/<slug>/response.json     Record, per-attempt
work/concepts/<slug>/validation.json   Record, per-attempt
wiki/index.md                          Projection, agent-facing
derived/index.json                     Projection, machine-facing
derived/navigation.json                Projection, render-facing
derived/vectors.npz                    Projection, search-facing
derived/stats.jsonl                    Projection, per-round build metrics
claims.db                              Query store, data-wave claims
wiki.db                                Query store + wiki graph
```

### Tool output contract

Tool stdout is terse text by default. This is the primary agent-facing
interaction style and should feel closer to `ls`, `grep`, and `cat` than to an
API response.

Examples:

```text
$ wikify wiki list
article  Atomic Layer Deposition
person   Stuart Parkin

$ wikify corpus find "ALD" --top-k 3
0.840  cites=12  chunk:5f92b0389ccd  doc:paper_a_short
0.790  cites=4   chunk:1a2b3c4d5e6f  doc:paper_b_short

$ wikify corpus show chunk:5f92b0389ccd
id:           chunk:5f92b0389ccd
doc:          doc:paper_a_short
section_type: methods
boilerplate:  False
section_path: ['Methods']
---
Atomic layer deposition...
```

`--format json` is the opt-in automation contract. In JSON mode, every tool
should return a small, predictable envelope:

```json
{
  "ok": true,
  "type": "concept",
  "id": "atomic-layer-deposition",
  "path": "work/concepts/atomic-layer-deposition/work.md",
  "events": ["01H..."],
  "counts": {}
}
```

Read tools in JSON mode return summaries unless `--detail` or `--full` is
provided:

```json
{
  "ok": true,
  "items": [
    {
      "id": "atomic-layer-deposition",
      "title": "Atomic Layer Deposition",
      "status": "ready",
      "path": "work/concepts/atomic-layer-deposition/work.md"
    }
  ]
}
```

Large outputs should still write a file and return the path, even in JSON mode.

### Best-practice constraints

- Keep verbs stable and generic. Add nouns only when there is a new durable
  bundle component.
- Do not add strategy-specific Python commands.
- Mutating tools require an active run context, resolved from `--run` or
  `./run/state.json`.
- Mutating tools append exactly one or more typed events.
- Mutating tools hold the run lock.
- Tools return handles and counts, not blobs.
- JSON/JSONL files are strict schemas; Markdown files are agent-facing control
  cards.
- Generated projections are rebuildable and never hand-edited.
- The same primitive should serve baseline, guided, and query-driven workflows.

### CLI implementation stack

Recommended stack:

```text
uv        project and command runner
Typer     command grammar and type-hint-driven options
Pydantic  file schemas and strict validation
Rich      optional human-readable output
```

Keep Typer as the primary CLI framework. The project already uses it, and it
fits the desired command shape: nested command groups, type hints, generated
help, shell completion, and thin wrappers around a typed Python API.

Use Click directly only if Typer blocks a required low-level behavior. Click is
the foundation under Typer and gives more control, but it costs more boilerplate
and makes the command code less declarative.

Use terse text output for default agent-facing commands, especially `list`.
The default should optimize for token-light `ls`-style inspection, not rich
tables or verbose JSON.

```text
--format text   default terse handles
--format json   explicit structured output for scripts/tests
--detail        add compact operational metadata
--full          return heavy content for one selected object
```

Examples:

```text
$ wikify wiki list
article  Atomic Layer Deposition
person   Stuart Parkin

$ wikify corpus list docs
doc:paper_a_short
doc:paper_b_short

$ wikify corpus list chunks --doc doc:paper_a_short
chunk:5f92b0389ccd
chunk:1a2b3c4d5e6f
```

Use JSON when the caller needs stable machine parsing:

```text
wikify wiki list --format json
wikify corpus find "ALD" --format json
```

Do not make Rich tables the default output for workflow commands. Rich is useful
for optional human-readable views, but the default skill path needs terse
handles, predictable lines, and explicit opt-in to larger payloads.

Do not use interactive prompt libraries such as Questionary in the core
agent-facing CLI. They are useful for optional human setup commands, but the
skill-driven path must be fully non-interactive and flag/file based.

Do not use Textual for the core workflow. A TUI could be useful later for
monitoring, but it should consume the same `run/events.jsonl`, `work/index.md`,
and `wiki/index.md` surfaces rather than becoming another control path.
