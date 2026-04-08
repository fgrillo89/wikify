# wikify_simple — code architecture and execution plan

This doc translates the design in `README.md`, `metrics.md`, and
`strategies.md` into a concrete package layout, a build order, and a
small set of coding rules. It supersedes the earlier sketches in
`models.py` and `distill/steps.py`, which were placeholders.

## Coding standards

These mirror the rules in `docs/refactor/wiki-deep-refactor-plan.md`
because the same standards should apply to wikify_simple from day one.

1. **Functions over namespace classes.** A `class` is justified only by
   shared mutable state or a real polymorphism need. Helpers are functions.
2. **Constructor injection over module-level globals.** Strategies, the
   cost meter, the cache, the context envelope, and the agent bindings
   are all passed in. There are no module-level mutable singletons.
3. **Modules ≤ 400 LOC.** Split anything that grows past 600.
4. **Protocols for real extension points.** `Extractor`, `Writer`,
   `Orchestrator`, `Embedder`, `ChunkStore`, `Sampler` are `Protocol`
   classes. Concrete implementations live in their own modules.
5. **Top-of-file imports.** No lazy imports except for genuinely optional
   dependencies (and even those should be questioned).
6. **Enums and dispatch tables over `if/elif` chains** when branching on
   stable kinds (sampler ops, tiers, roles).
7. **No vendor-specific names in module names or public symbols.** No
   `claude_*`, `anthropic_*`, `openai_*`. Vendor identity is
   configuration. The only exception is the binding module file itself
   (`bindings/anthropic.py`) which is the *one* place that imports the
   vendor SDK.
8. **No grab-bag helper modules.** Helpers belong in the nearest
   responsible package.
9. **Agent-native core.** No core module imports an LLM SDK. The
   orchestrator (Python harness) supplies model behavior through
   injected `Extractor` / `Writer` / `Orchestrator` protocols.
10. **One responsibility per module**, stated in a one-line top-of-file
    docstring.
11. **No backward-compatibility shims.** wikify_simple is fresh; if a
    file is wrong it gets edited, not deprecated.

## Package layout

```
src/wikify_simple/
  __init__.py
  README.md                 # design spine
  metrics.md                # the four order parameters + GT-P + GT-C
  strategies.md             # the cube + four anchors + ablations
  architecture.md           # this file

  models.py                 # the typed core: Document, Chunk, CorpusGraph,
                            # Evidence, WikiPage, WikiGraph, Stage, Run
  paths.py                  # the only module that knows where things live

  infra/                    # shared infrastructure (no LLM, no domain logic)
    __init__.py
    cost_meter.py           # CostMeter: per-call accounting + budget gate
    cache.py                # ExtractCache: deterministic per-chunk cache
    context_envelope.py     # ContextEnvelope: priority-fill prompt builder
    tokens.py               # tokeniser-agnostic token-count helper
    role.py                 # Role enum + per-role spec lists

  agents/                   # protocols only; no concrete LLM code
    __init__.py
    protocols.py            # Extractor, Writer, Orchestrator (Protocol)
    schema.py               # the structured request/response shapes

  bindings/                 # the only place model dispatch lives
    __init__.py
    fake.py                 # deterministic fakes for tests + dry runs
    claude_code.py          # the only file that talks to the Claude Code
                            # subagent dispatcher (writes/reads request and
                            # response files at well-known paths)

  store/                    # disk I/O for corpus and wikis
    __init__.py
    corpus.py               # read documents/chunks/embeddings
    vectors.py              # thin vector-db wrapper
    wiki_files.py           # read/write wiki page .md files (frontmatter
                            # + body + evidence footnotes); mirror of the
                            # eval/bundle.py loader

  ingest/                   # corpus build (parsers, chunking, embedding,
                            # image extraction, corpus graph)
    __init__.py
    parsers/                # one parser per kind, returns markdown +
                            #   image refs + section tree
      pdf.py
      docx.py
      pptx.py
      html.py
      markdown.py
      registry.py
    chunker.py              # markdown -> [Chunk]
    images.py               # extract figures + captions + slide images;
                            # produce DocImage records with caption text
    embedder.py             # writes chunk + image-caption embeddings to the
                            # vector store
    corpus_graph.py         # builds CorpusGraph: contains, similar_knn,
                            # similar_strong (cosine >= 0.75), co_section,
                            # cites (when present), doc_similar
    topics.py               # the cleaned-and-sanitised port of the existing
                            # PaperTopic extraction (used by GT-C in eval)
    refresh.py              # idempotent corpus refresh entry point

  distill/                  # the strategies and their primitives
    __init__.py
    sampler.py              # Sampler protocol + (local_op, global_op,
                            # jump_rate) implementation
    schedule.py             # Schedule protocol + exploit_fraction +
                            # adaptive switch
    canonicalize.py         # deterministic merge of candidates → pages
    crosslink.py            # post-write linker
    pipeline.py             # the fixed list of stages every cell runs
    strategies/
      __init__.py
      explore.py            # cell E
      mixed.py              # cell M (the headline)
      exploit.py            # cell X
      agent.py              # the model-driven cell

  eval/                     # already exists from a prior round
    __init__.py
    bundle.py
    metrics.py

  cli.py                    # thin Typer adapter; one command per verb
```

Three things to call out:

- **`infra/` is pure Python.** No LLM, no domain logic. Cost meter, cache,
  context envelope, tokeniser shim, role enum. These three pieces are the
  contract between strategies and any model.
- **`agents/` is Protocols only.** It defines the shape of extractor /
  writer / orchestrator calls but contains no implementation. Strategies
  depend on `agents/protocols.py`, never on `bindings/*`.
- **`bindings/` is the dispatch boundary.** `claude_code.py` is the only
  file in the entire package that talks to the Claude Code subagent
  dispatcher. A lint rule enforces that no other module shells out via
  the Task tool, the SDK, or any direct model API. If we ever add an
  SDK fallback, it lands as a sibling `bindings/sdk.py` and the binding
  selection becomes a CLI flag.

## The dependency direction

```
                          models.py
                              ^
                              |
        +---------------------+---------------------+
        |                     |                     |
       infra/               store/              eval/
        ^                     ^                     ^
        |                     |                     |
        +----+----+           |                     |
             |    |           |                     |
        agents/  paths.py     |                     |
             ^                |                     |
             |                |                     |
        +----+-----+----------+                     |
        |                                           |
     distill/                                       |
        ^                                           |
        |                                           |
     bindings/  <-- only place that talks to Claude Code dispatch
        ^
        |
      cli.py  <-- thin adapter; wires bindings into distill
```

Strategies in `distill/strategies/*` depend on `agents/protocols.py` and
`infra/`. They never import `bindings/`. The CLI is the one place that
wires a concrete binding (real `anthropic.py` or `fake.py`) into the
strategy at run time.

## Key types and protocols

The full types live in `models.py` (the eight already-drafted dataclasses)
and in `agents/protocols.py`. Sketch only here:

```python
# agents/protocols.py
class Extractor(Protocol):
    def extract(self, request: ExtractRequest) -> ExtractResponse: ...

class Writer(Protocol):
    def write(self, request: WriteRequest) -> WriteResponse: ...

class Orchestrator(Protocol):
    def step(self, state: OrchState) -> OrchAction: ...
```

`ExtractRequest` carries the `target_chunk` and the `canonical_titles`
pool. The Protocol does *not* expose tokens or models — it exposes
*content*. The binding is responsible for turning content into a model
call. The strategy never sees the SDK.

```python
# distill/sampler.py
class Sampler(Protocol):
    def next_batch(self, state: SamplerState, k: int) -> list[ChunkRef]: ...

@dataclass(frozen=True)
class LevyMixSampler:
    local_op: LocalOp                     # enum
    global_op: GlobalOp                   # enum
    jump_rate: float
    chunks_per_landed_doc: int = 3

    def next_batch(self, state, k):
        out = []
        for _ in range(k):
            if state.wiki_is_empty or state.rng.random() < self.jump_rate:
                out.extend(self._global(state))
            else:
                out.append(self._local(state))
        return out
```

`local_op` and `global_op` are dispatched via small dispatch tables in
the same module, not `if/elif` chains.

## Build order

Six slices, each ≤ 1 day of focused work, each independently testable.

### Slice 0 — ingest port

**Files added**: `ingest/parsers/*`, `ingest/chunker.py`, `ingest/images.py`,
`ingest/embedder.py`, `ingest/corpus_graph.py`, `ingest/topics.py`,
`ingest/refresh.py`, `store/corpus.py`, `store/vectors.py`.

**Tasks**:
- Port the existing `wikify.ingest` parsers (pdf, docx, pptx, html, md)
  into `wikify_simple/ingest/parsers/` with no behaviour change.
- Add `images.py`: every parser now emits `DocImage` records with
  `path`, `caption`, `alt_text`, `page`, and `near_chunk_ids`. Captions
  are stored as plain text and embedded alongside chunks.
- Add `corpus_graph.py`: materialise the six edge kinds from
  `models.py::CorpusGraph` (`contains`, `similar_knn`, `similar_strong`
  with cosine ≥ 0.75, `co_section`, `cites`, `doc_similar`).
- Port the existing `PaperTopic` extraction into `topics.py` with the
  sanitisation rules from `metrics.md` (df > 0.5 × n_docs dropped, df < 3
  dropped, stop-phrase blacklist, re-run `_deduplicate_topics`).
- `refresh.py` is the single entry point: `ingest_corpus(input_dir,
  output_dir)` produces a complete corpus on disk plus the topic table
  needed by GT-C.

**Acceptance**:
- Running `wikify-simple ingest tests/fixtures/tiny/` produces a corpus
  directory with `markdown/`, `images/`, `chunks/`, `graph.json`, and a
  topic vocabulary file.
- Every chunk has an embedding; every image caption has an embedding.
- Smoke run on five real PDFs, one docx, one pptx, one html, one md.

**Forbidden**: any LLM call (ingestion is deterministic + cheap models
only — captioning, OCR, embedding); reading from `wikify.ingest` at run
time (the port is one-shot, not a wrapper).

### Slice 1 — shared infrastructure

**Files added**:
`infra/role.py`, `infra/tokens.py`, `infra/context_envelope.py`,
`infra/cost_meter.py`, `infra/cache.py`.

**Acceptance**:
- `ContextEnvelope.build(role, slots)` returns a string ≤ effective cap
  for every Role; floors are respected; over-budget items become summary
  placeholders.
- `CostMeter.record(...)` accumulates correctly; `meter.spent_haiku_eq`
  gates a hard abort at 1.05× budget; live status prints to stderr.
- `ExtractCache.get_or_compute(key, compute)` returns cached values
  byte-for-byte and only invokes `compute` on miss.
- All three modules have unit tests using fakes — no LLM, no network.

**Forbidden**: importing `anthropic`, importing anything from `distill/`,
referring to specific tier names by hard-coded strings.

### Slice 2 — agents and bindings

**Files added**:
`agents/protocols.py`, `agents/schema.py`, `bindings/fake.py`,
`bindings/claude_code.py`.

**Acceptance**:
- `Extractor`, `Writer`, `Orchestrator` Protocols defined with typed
  request/response dataclasses.
- `bindings/fake.py` implements all three deterministically: an
  extractor that returns one canned concept per chunk, a writer that
  echoes the skeleton, an orchestrator that picks `done` after N steps.
- `bindings/claude_code.py` writes a request file, blocks for the
  matching response file at a well-known path, parses the JSON,
  validates against the schema in `agents/schema.py`, deducts from
  the CostMeter, consults `ExtractCache.get_or_extract()` for
  extraction calls (cache check happens *inside* the binding so it
  is invisible to the calling strategy and to the agent skill).
- The cache returns instantly on hit *without* writing a request
  file or invoking any subagent. Cache hits are zero-token.
- A lint check fails if any module other than `bindings/claude_code.py`
  references the request/response file paths or the dispatcher.

**Forbidden**: any business logic in `bindings/*`. The bindings are
adapters; canonicalisation, sampling, and pipeline orchestration live
elsewhere.

### Slice 3 — distill primitives

**Files added**:
`distill/sampler.py`, `distill/schedule.py`, `distill/canonicalize.py`,
`distill/crosslink.py`, `distill/pipeline.py`.

**Acceptance**:
- `Sampler` Protocol + `LevyMixSampler` dataclass with `local_op`,
  `global_op`, `jump_rate`.
- `Schedule` Protocol + static and adaptive variants.
- `canonicalize(candidates, existing_pages) → list[WikiPage]` is pure
  python, deterministic, no LLM.
- `crosslink(pages) → pages` populates `links` via embedding kNN +
  alias matching.
- `pipeline.run(corpus, sampler, schedule, extractor, writer, ...)`
  is the fixed eight-step loop. No strategy-specific logic; all
  strategy variation comes from the injected sampler/schedule/binding.

**Forbidden**: any LLM call, any vendor name, any hard-coded prompt.

### Slice 4 — strategies E, M, X

**Files added**:
`distill/strategies/explore.py`, `mixed.py`, `exploit.py`,
`distill/strategies/__init__.py`.

Each file is a thin assembly: pick the sampler, the schedule, the
tiering, hand them to `pipeline.run`. None of them is over ~80 lines.

**Acceptance**:
- All three cells produce a complete `data/wikis/{cell}_1x_seed0/`
  bundle on a 5-document fixture corpus, using `bindings/fake.py`.
- The eval harness (`eval/`) reads each bundle and produces all four
  order-parameter scalars and two reference scalars without errors.
- M6 grounding gate passes on all three (with the fake binding, the
  evidence quotes are tautologically valid).

**Forbidden**: strategy-specific extensions to `pipeline.run`. If a
strategy wants something the pipeline doesn't provide, change the
pipeline (and update the other strategies).

### Slice 5 — CLI and agent skills

**Files added**:
`cli.py`, `.claude/skills/wikify_simple/extract.md`,
`.claude/skills/wikify_simple/write.md`,
`.claude/skills/wikify_simple/orchestrate.md`,
`.claude/skills/wikify_simple/distill.md`.

**Acceptance**:
- `cli.py` exposes `wikify-simple distill --strategy {E|M|X} --budget 1x
  --seed 0 --binding {fake|claude_code}` and the helper verbs
  `binding-prepare` and `binding-receive` that the skills shell out to.
- The `extract.md` skill is a five-step mechanical recipe with no
  judgment: prepare the request file, spawn one Task subagent with the
  exact prompt and JSON schema from the file, receive the response,
  hand it back to Python.
- `write.md` and `orchestrate.md` follow the same shape.
- `distill.md` is the meta verb the user invokes for the agent cell.
  It calls the harness in agent mode and uses the other three skills
  via Claude Code's tool layer. The agent cell has no Python loop —
  the loop is the Claude Code session itself.
- A full smoke run on the fixture corpus with `--binding fake` produces
  a bundle for every deterministic cell (E, M, X) in under 30 seconds,
  with no Task subagent calls (the fake binding short-circuits).

**Forbidden**: the CLI containing any business logic. The skills
containing any judgment. The agent strategy as a separate Python
file — it does not exist; the skills are the strategy.

### Slice 6 — first real run (calibration)

Once slices 1–5 are landed, the first real run is on a ~20-document
calibration corpus, in this exact order:

```bash
wikify-simple distill --strategy E --budget 1x --seed 0 --binding claude_code
wikify-simple distill --strategy M --budget 1x --seed 0 --binding claude_code
wikify-simple distill --strategy X --budget 1x --seed 0 --binding claude_code
# then, in an interactive Claude Code session with the wikify_simple
# skills loaded, the user invokes:
/wikify-simple-distill --strategy agent --budget 1x --seed 0
```

The order matters: E, M, X populate the on-disk extraction cache so
that the agent run hits warm cache for every chunk a deterministic
cell already touched. The agent's wall-clock cost is then dominated
by the chunks it uniquely chooses, which is small.

Look at the four bundles, run the eval harness, look at the curves.
Decide whether the design holds before committing to the 33-cell sweep.

## The cache is invisible to the agent

A single rule for the cache: **`infra/cache.py` exposes one function,
`get_or_extract(key, compute) -> ExtractResponse`**. There is no public
`get` and no public `put`. There is no way to use the cache wrong.

- Deterministic strategies (E, M, X) call `get_or_extract` directly
  before constructing a request. On hit, no Task subagent is spawned.
  On miss, the binding dispatches and the result is stored.
- The agent strategy's `extract_chunk` skill calls the same function
  via `bindings/claude_code.py`. The agent's mental model is just
  "extract is fast on chunks I've seen before". The cache name appears
  in zero skill files.
- A deterministic-cell run pre-warms the cache for any subsequent
  agent-cell run on the same corpus. This is what makes the agent
  cell affordable in wall-clock terms under the subscription model.

## Testing strategy

- **Slice 1** unit-tests every helper with stdlib `unittest` (or pytest)
  using only Python data — no fakes needed yet.
- **Slice 2** uses `bindings/fake.py` to integration-test the protocols
  end-to-end without any network calls.
- **Slices 3–4** run a fixture-corpus smoke test (`tests/fixtures/tiny/`,
  five hand-built `Document`s) under `--binding fake`. The eval harness
  numbers on these fixtures are the regression target: any change to
  pipeline or sampler must keep them stable.
- **Slice 5** adds an integration test that runs the agent strategy on
  the same fixture under `--binding fake` and verifies the action trace
  contains every action verb at least once.
- **No live API in any test.** Real-API runs happen only in slice 6 and
  beyond, behind an explicit `--binding anthropic` flag and a CLI gate
  that requires `WIKIFY_SIMPLE_ALLOW_NETWORK=1`.

## Things deliberately not in v1

- **The ingest port is its own slice (slice 0), not a from-scratch
  rewrite.** It refactors the existing `wikify.ingest` modules into
  `wikify_simple/ingest/` to match the new data model — adding first-class
  image units, the `similar_strong` and `doc_similar` corpus-graph edges,
  and the cleaned-up topic extraction used by GT-C. It does not redesign
  parsing.
- **No vector-db replacement.** `store/vectors.py` is a thin wrapper over
  whichever vector store the existing ingest writes to.
- **No web UI, no dashboard.** Bundles are markdown files; eval outputs
  are scalars. Plot them in a notebook.
- **No multi-corpus orchestration.** The CLI runs one strategy on one
  corpus at one budget. Sweeps are shell loops over the CLI.
- **No susceptibility (M4)** in the headline study, except for the
  `agent` cell where we already committed to 2 seeds.

## Open questions before slice 1 starts

1. **Tokeniser source**. `infra/tokens.py` needs *some* tokeniser to
   compute envelope sizes. Options: tiktoken (vendor-neutral but Python
   3.13 wheels can lag), sentencepiece, or a 4-char-per-token rule of
   thumb. My lean: rule-of-thumb for slice 1 (good enough for envelope
   accounting), real tokeniser if accuracy ever matters.
2. **Pricing config location**. Tier prices live in one yaml at
   `data/pricing.yaml`, loaded once by the cost meter. When pricing
   changes, edit one file. Yes/no.
3. **Run id format**. Proposal: `{cell}_{budget}x_seed{n}_{utc_ymd_hms}`.
   Sortable, human-readable, no collisions across runs. Yes/no.
4. **Where the ContextEnvelope spec lists live**. One file
   (`infra/context_envelope.py`) with three top-level constants, or one
   file per role under `infra/specs/`? My lean: one file, three
   constants, ~120 LOC total. Splitting is overkill.
5. **Should `pipeline.run` be a function or a class?** A function
   matches the coding standards better. The reason to consider a class
   is that the pipeline holds the cost meter, the cache, and the
   strategy state. My lean: function with explicit args, all state
   passed in. Tests stay simpler.
