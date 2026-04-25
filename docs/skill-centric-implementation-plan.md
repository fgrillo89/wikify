# Skill-centric implementation plan

This plan turns the filesystem-state design into an implementation sequence.
The target architecture is skill-driven: Python exposes deterministic tools,
skills own strategy, and agents interact with bundle state through a small
shell-like CLI.

## Target shape

The repo should converge on five core runtime nouns:

```text
run
corpus
work
draft
wiki
```

The common read grammar should mimic shell tools:

```text
list          ls
find --text   grep / rg
find          semantic or graph-aware retrieval
show          cat, with parsed text/YAML views
```

Mutation and lifecycle verbs stay narrow:

```text
add
set
build
check
commit
tend
close
```

Agents should not read raw JSON/JSONL during normal runs. They should call CLI
views that render compact text by default and use `--format json` only for
automation or tests. Direct `ls`, `rg`, and `cat` are debugging escape hatches;
skill workflows should use the CLI wrappers so access is constrained and logged.

## Repository redesign

The existing modules can be migrated without a flag-day rewrite. New surfaces
should wrap current internals first, then old commands can be retired.

```text
src/wikify/
  api.py                  Bundle facade for tests and deterministic helpers
  paths.py                BundlePaths / CorpusPaths only
  cli.py                  Thin Typer app wiring noun groups
  cli_io.py               Process-boundary CLI IO telemetry

  cli_cmds/
    run.py                run init/show/list/set/close/lock/unlock
    corpus.py             corpus build/refresh/list/find/show/check
    work.py               work list/find/show/add/set/tend
    draft.py              draft build/show/check
    wiki.py               wiki list/find/show/build/check/commit
    eval.py               eval/report commands, downstream only
    render.py             render/html commands, downstream only

  stores/
    run.py                RunStore: state, events, lock, IO telemetry
    corpus.py             CorpusStore: read-only corpus access
    work.py               WorkStore: concepts, evidence, inbox, tending
    draft.py              DraftBuilder and draft renderers
    wiki.py               WikiStore: committed pages and promotion
    derived.py            rebuildable indexes/graphs/vectors

  ingest/                 keep current pipeline, expose via corpus build/refresh
  citestore/              keep fluent corpus KG internals
  store/wiki_graph.py     keep fluent wiki KG internals
  eval/                   consume bundles, never mutate them
  render/                 consume bundles, never mutate them
```

Current compatibility commands can remain temporarily:

```text
session -> run
kg      -> corpus
bundle  -> wiki
validate write -> draft check
ingest  -> corpus build
refresh -> corpus refresh
html    -> render
```

Compatibility commands should call the new store/API layer, not fork logic.

## Filesystem migration

Current bundle layout:

```text
articles/
people/
_index.json
_index.md
_wiki_graph.json
_run.json
_run_history.jsonl
_calls.jsonl
_session/
_scratch/
_meta/
```

Target bundle layout:

```text
wiki/
  index.md
  articles/
  people/

work/
  index.md
  inbox/
  concepts/<slug>/
    work.md
    evidence.jsonl
    draft.json
    response.json
    validation.json

run/
  state.json
  events.jsonl
  lock
  io/

derived/
  index.json
  graph.json
  vectors.npz
  vectors.ids.json
```

Migration should be additive at first:

```text
1. Add target paths while keeping old paths readable.
2. Write new runs to the target layout.
3. Teach render/eval to read target layout.
4. Add a one-way migration command for old bundles.
5. Remove old layout only after tests and skills are migrated.
```

## CLI implementation phases

### Phase 1: shared foundation

Implement a typed internal API and path layer:

```python
bundle = Bundle.open(path)
bundle.run.show()
bundle.corpus.find("ALD")
bundle.work.add("concept", "Atomic Layer Deposition", kind="article")
bundle.draft.build("atomic-layer-deposition", task="create")
bundle.wiki.commit("atomic-layer-deposition")
```

Acceptance criteria:

- CLI commands are thin adapters.
- All mutating commands require an active run context.
- Read commands default to text.
- `--format json` is stable and covered by tests.
- CLI IO logging captures argv/stdin/stdout/stderr for skill-driven runs.

### Phase 2: corpus CLI

Expose ingest and exploration through `corpus`:

```text
wikify corpus build <source> --out <corpus>
wikify corpus refresh <corpus>
wikify corpus check [<corpus>]
wikify corpus list docs|chunks|authors|files
wikify corpus find "query" [--text] [--in doc:<id>|cited-by:<id>]
wikify corpus find --seed
wikify corpus find --cited-by <doc>
wikify corpus show doc:<id>|chunk:<id>|<relative-path>
```

Implementation notes:

- Reuse `ingest.pipeline`, `distill.preload`, `citestore.graph`, and vector
  stores.
- `find --text` should be a bounded grep over corpus markdown/chunks/metadata.
- Default `find "query"` should return scored chunk handles and previews.
- `show chunk:<id> --full` is the only normal way to emit full chunk text.

### Phase 3: wiki CLI

Expose committed wiki pages and wiki KG:

```text
wikify wiki list [articles|people|pages|files]
wikify wiki find "query" [--text]
wikify wiki find --links <page>
wikify wiki find --linked-by <page>
wikify wiki find --co-evidence <page>
wikify wiki find --orphans
wikify wiki find --overlaps <page>
wikify wiki show <page-or-relative-path> [--full]
wikify wiki build indexes|graph|vectors
wikify wiki check
wikify wiki commit <concept>
```

Implementation notes:

- Exact title/alias lookup should run before semantic search.
- `find --text` should return path, line, and snippet.
- `show` should render Markdown pages directly but JSON projections as compact
  text/YAML.
- `commit` is the only command that promotes work into committed wiki pages.

### Phase 4: work and draft CLI

Implement live wikification state:

```text
wikify work list [--status ready]
wikify work list evidence <concept>
wikify work list inbox [kind]
wikify work find "query" [--text]
wikify work show <concept-or-relative-path>
wikify work add concept "<title>" --kind article|person
wikify work add evidence <concept> --chunk <chunk>
wikify work add evidence <concept> --records <jsonl>
wikify work add feedback query --record <json-or-path>
wikify work set <concept> --status needs_refine
wikify work tend

wikify draft build <concept> --task create|refine
wikify draft show <concept>
wikify draft check <concept>
```

Implementation notes:

- `work.md` is the compact agent-facing control card.
- `evidence.jsonl` is the long-lived evidence ledger.
- `draft.json`, `response.json`, and `validation.json` are per-attempt
  artifacts and GC-eligible after commit/refine.
- `work tend` owns inbox consolidation, compaction, deduplication, status
  updates, and GC.

### Phase 5: skills

Split skills by responsibility:

```text
wikify-ingest
  Build or refresh a corpus from source files.

wikify-baseline
  Baseline concept extraction -> evidence retrieval -> writing -> commit.

wikify-query
  Answer from wiki/corpus and emit feedback that improves the wiki.

wikify-maintain
  Tend, compact, refine, replay, and inspect telemetry.
```

Each skill should teach the same command grammar, not private APIs.

## MVP 1: baseline build

Goal: from existing corpus to committed wiki pages using the new bundle
contract and CLI wrappers.

Minimal commands:

```text
wikify run init --bundle <bundle> --corpus <corpus> --strategy baseline
cd <bundle>
wikify corpus find --seed --max <n>
wikify corpus show chunk:<seed> --full
wikify work add concept "<title>" --kind article|person
wikify corpus find "<title>" --top-k 8 --out work/tmp/<slug>-evidence.jsonl
wikify work add evidence <slug> --records work/tmp/<slug>-evidence.jsonl
wikify work tend
wikify draft build <slug> --task create
agent writes work/concepts/<slug>/response.json
wikify draft check <slug>
wikify wiki commit <slug>
wikify run close --status completed
```

MVP shortcuts:

- Concept extraction can initially reuse the current extract response schema and
  canonicalizer.
- Writer agents can keep producing current `WriteResponse` payloads.
- `draft check` can wrap the existing `validate write` logic.
- `wiki commit` can wrap the existing bundle promotion logic.

Acceptance criteria:

- One corpus produces at least one committed article.
- All source quotes are grounded in corpus chunks.
- `wiki/index.md`, `derived/index.json`, and `derived/graph.json` rebuild.
- `run/events.jsonl` records concept creation, evidence add, draft creation,
  validation, commit, and run close.
- CLI IO logs let replay reconstruct what the agent saw.
- No agent step reads raw JSON directly except the writer response artifact.

## MVP 2: query mode

Goal: answer a user question and use gaps to improve the wiki through the same
work lifecycle.

Minimal commands:

```text
cd <bundle>
wikify wiki find "<question>"
wikify wiki show "<best-page>" --full
wikify corpus find "<question>" --top-k 8
wikify corpus show chunk:<chunk> --full
agent answers user
wikify work add feedback query --record work/tmp/query-feedback.json
wikify work tend
wikify work list --status ready
wikify draft build <slug> --task create|refine
agent writes response.json
wikify draft check <slug>
wikify wiki commit <slug>
```

Query feedback record should capture:

```text
query
answer_source: wiki | corpus | wiki+corpus
affected_pages
missing_concepts
gap
evidence_chunks
severity
```

Acceptance criteria:

- Query can answer from wiki only when sufficient.
- Query falls back to corpus when the wiki is thin or missing.
- Query feedback lands only in inbox/work, never directly in committed wiki.
- `work tend` can convert feedback into new evidence, new concepts, or
  `needs_refine`.
- Refinement draft reads current wiki page plus active evidence.
- Committed page is updated through the same validation and commit gate.

## Testing plan

Unit tests:

- path resolution and bundle-root safety
- text renderers for JSON/JSONL artifacts
- `list/find/show` output limits
- lexical `find --text`
- corpus graph wrappers
- wiki graph wrappers
- work evidence deduplication
- inbox consolidation
- draft build and validation schemas
- CLI IO telemetry

Integration tests:

- ingest skill smoke: source files -> corpus check passes
- baseline MVP: corpus -> one committed page
- query MVP: question -> answer + feedback -> refinement
- replay: events + CLI IO reconstruct command timeline
- migration: old bundle layout can be read or migrated

Regression tests:

- no raw JSON required for normal agent reads
- mutating commands require run context and lock
- commands cannot read outside bundle/corpus roots
- `--full` required for large content
- output defaults remain terse text

## Migration order

1. Add new path classes and stores beside current code.
2. Add new `corpus`, `run`, `work`, `draft`, and `wiki` command groups.
3. Make old commands call new internals.
4. Add skills for ingest, baseline, query, and maintenance.
5. Port baseline e2e tests to new command grammar.
6. Port render/eval to target bundle layout.
7. Add migration command for old bundles.
8. Remove old command docs and compatibility paths after parity.

## Non-goals for the MVP

- No full graph query language.
- No strategy-specific Python workflow commands.
- No direct Python model calls.
- No hidden global current-corpus config.
- No hand-edited derived indexes.
- No rich terminal tables as default output.
