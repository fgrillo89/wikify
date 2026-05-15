# End-to-end Wikify plan

Status: proposed (2026-05-14)

## Objective

Ship a first end-to-end Wikipedia-style wiki generated from a corpus,
rendered as a usable static HTML site, using a simple conventional
RAG-based strategy.

That first shipped artifact is the baseline. After it exists, Wikify can
study exploration and writing strategies by running different skill
recipes over the same corpus/budget/eval harness and comparing quality,
cost, wall-clock, telemetry, and evaluation metrics.

The project goal is not just "make pages once." The durable goal is a
skill-driven laboratory for scientific-article wikification:

- skills own exploration and writing strategy;
- Python exposes deterministic corpus, bundle, draft, wiki, render, and
  eval primitives;
- every run leaves enough telemetry to compare strategies;
- every committed page passes citation grounding;
- the final wiki is browsable and polished enough to inspect as a real
  product, not only as markdown files.

## Non-negotiable design constraints

- Strategy lives in `.claude/skills/wikify-*`, not in Python workflow
  controllers.
- The simple baseline should be boring: sample/search, collect evidence,
  write, validate, commit, render, eval.
- `run/events.jsonl` remains the canonical telemetry ledger. Any
  retrieval trace or visualization export is derived from events unless
  the architecture explicitly promotes a second artifact.
- The first run prioritizes one complete wiki bundle over advanced
  retrieval features.
- The renderer is part of the product surface. A run is not end-to-end
  until `wikify render` produces a static site that can be inspected.

## Phase 0 - Pick the first corpus and acceptance target

Goal: define the exact "first wiki" so the workflow has a concrete
finish line.

Tasks:

1. Pick and pin one corpus snapshot, preferably the current ALD Docling
   corpus under `data/corpora/ald_docling_*`.
2. Record the corpus path and manifest hash in the run notes or baseline
   report. Do not use "latest" for the artifact run.
3. Choose a modest target size:
   - 10-20 article pages;
   - optional person pages only if bibliography metadata is clean enough;
   - no query-driven refinement in the first run.
4. Define minimum acceptance:
   - pages are full encyclopedic articles, not stubs;
   - citation validation passes at `draft check` and `wiki commit`;
   - `wikify wiki check` passes;
   - `wikify render --format html` writes a browsable site;
   - `wikify eval --bundle ... --corpus ...` writes `derived/eval.json`;
   - run closes with telemetry and cost summary.

Exit criteria:

- A pinned corpus and target bundle name are written in the run notes.
- The first-wiki acceptance checklist is explicit and small enough to
  complete before adding new retrieval features.

## Phase 1 - Sharpen the simple RAG baseline skill

Goal: make `wikify-baseline` a reliable end-to-end recipe that an agent
can follow without inventing missing workflow decisions.

The baseline strategy:

1. Initialize/open a bundle.
2. Sample diverse documents from the corpus.
3. Extract candidate article/person concepts from sampled text.
4. Add accepted concepts to `work/`.
5. For each concept, run conventional retrieval:
   - `corpus find "<concept>" --rank all --top-k N`;
   - `corpus show chunk:<id> --full` for selected chunks;
   - optionally one-hop `corpus traverse` only when needed for context.
6. Add grounded evidence to the concept ledger.
7. Build the draft input.
8. Invoke writer subagents through `wikify-write-page`.
9. Check, commit, release, and tend.
10. Rebuild projections, render HTML, evaluate, and close the run.

Skill work:

- Update `wikify-baseline/SKILL.md` so the end-to-end loop includes
  render and eval as first-class final steps.
- Make baseline defaults explicit for the first run:
  - sample count;
  - evidence `top_k`;
  - maximum concepts;
  - writer tier;
  - writer concurrency;
  - claim TTL;
  - retry/escalation behavior.
- Tighten the evidence-gathering instructions so the skill records why
  each chunk was selected and avoids dumping low-value retrieval hits
  into the writer.
- Ensure the writer skill still produces Wikipedia-style prose, natural
  titles, no visible wikilinks in prose, and citation definitions with
  verbatim source quotes.
- Add a brief "first artifact mode" section: no graph-RAG, no summaries,
  no reranker, no query refinement, no figure/equation citation changes.

Exit criteria:

- The baseline skill can be followed from empty bundle to rendered site.
- The skill makes no hidden strategy assumptions that only live in a
  task plan.
- Running the baseline does not require new Python strategy code.

## Phase 2 - Verify the mechanical pipeline end to end

Goal: prove the existing deterministic primitives support the first
artifact before broadening strategy research.

Checks:

1. Run lifecycle:
   - `wikify run init`
   - `wikify run show`
   - `wikify run close`
2. Work lifecycle:
   - `work add concept`
   - `work add evidence`
   - `work claim/release`
   - `work tend`
3. Draft/wiki lifecycle:
   - `draft build`
   - writer writes `response.json`
   - `draft check`
   - `wiki commit`
   - `wiki build indexes|graph|vectors` where required
4. Render:
   - `wikify render --bundle <bundle> --format html`
   - inspect generated `derived/site/index.html`;
   - verify article pages, people pages, references, navigation, and
     links render correctly.
5. Eval:
   - `wikify eval --bundle <bundle> --corpus <corpus>`;
   - verify M1, M3, M5, M6, telemetry, and cost fields are populated
     or explicitly unavailable.

Render quality checks:

- The static site should feel like a compact encyclopedia, not a debug
  dump.
- Pages need readable typography, clear reference formatting, working
  navigation, and usable index pages.
- Missing figures/equations should degrade gracefully.
- Render output is deterministic and read-only.

Exit criteria:

- A smoke-sized bundle can be built, committed, rendered, and evaluated.
- Any blocker is fixed in the smallest deterministic primitive that owns
  it, not papered over in the skill.

## Phase 3 - Ship the first end-to-end wiki

Goal: create the first real artifact with simple RAG.

Run shape:

- Corpus: pinned Phase 0 corpus.
- Strategy: `wikify-baseline`.
- Target: 10-20 committed article pages.
- Retrieval: conventional hybrid/BM25/semantic retrieval through
  `corpus find --rank all`, with selected chunks shown before evidence
  insertion.
- Writing: current `wikify-write-page` contract.
- Validation: `draft check` and `wiki commit` gates.
- Output:
  - committed wiki under `wiki/`;
  - rendered site under `derived/site/`;
  - eval report under `derived/eval.json`;
  - telemetry under `run/events.jsonl`.

First artifact report:

- bundle path;
- corpus path and manifest hash;
- strategy name and skill revision;
- number of pages committed/failed;
- cost in haiku-equivalent tokens;
- wall-clock time;
- M1, M3, M5, M6;
- known content gaps;
- render inspection notes.

Exit criteria:

- The generated HTML site is the primary artifact for human inspection.
- The markdown wiki, eval report, and telemetry are complete enough to
  reproduce and critique the run.
- The run gives a concrete baseline for later strategy comparisons.

## Phase 4 - Strengthen eval and telemetry for strategy science

Goal: after the first artifact exists, make comparisons rigorous.

Near-term eval work:

- Ensure `wikify eval` reports the current core metrics cleanly:
  - M1 coverage residual;
  - M3 graph crystallinity on evidence/link graphs;
  - M5 hit rate from `chunk_read` events;
  - M6 grounding gate;
  - telemetry/cost rollup.
- Add or finish GT-P and GT-C reporting if the corpus metadata supports
  them.
- Make eval output stable enough for a run matrix: one JSON report per
  bundle, plus a small comparison script/report if needed.

Telemetry work:

- Keep `run/events.jsonl` as the source of truth.
- Audit retrieval-producing surfaces so CLI and MCP reads emit comparable
  telemetry where the active bundle is known.
- If retrieval recall is needed, first define an event-level schema for
  ranked retrieval candidate sets:
  - query;
  - tool/mode/ranker;
  - concept/page target when known;
  - `top_k`;
  - ordered candidate chunk ids with scores;
  - selected evidence ids, if selection happens in the same step.
- Only then add an M7-style retrieval recall metric. Do not add a metric
  before the producer is trustworthy.

Run-matrix requirements:

- Pin corpus snapshot.
- Run multiple budget points.
- Run multiple seeds where the workflow has stochastic choices.
- Compare curves over cost, not only one-off scalar results.

Exit criteria:

- Eval can compare at least two bundles without manual spreadsheet work.
- Missing metrics are reported as unavailable, not zero.
- The telemetry needed by M5 and any future M7 has an end-to-end test.

## Phase 5 - Create explicit strategy variants in skills

Goal: turn Wikify into a controlled exploration/writing strategy lab.

Do this after the simple baseline artifact ships.

Initial strategy cells:

- `baseline-simple-rag`: fixed sample, fixed retrieval, fixed writer.
- `baseline-citation-walk`: conventional retrieval plus citation walk
  expansion.
- `guided-simple-rag`: model-guided choice of next concept/search while
  using the same conventional retrieval primitives.
- `guided-gap-fill`: guided loop that reads current wiki/work state and
  chooses whether to add a concept, add evidence, refine, or stop.

Skill requirements:

- Each strategy has explicit:
  - loop shape;
  - allowed retrieval primitives;
  - budget policy;
  - writer tier;
  - concurrency;
  - stop criteria;
  - retry policy.
- Shared mechanics stay in capability skills:
  - `wikify-search-corpus`;
  - `wikify-search-wiki`;
  - `wikify-write-page`;
  - `wikify-bundle`.
- New strategies should be new workflow skills or clearly named skill
  sections, not Python controllers.

Exit criteria:

- At least two strategy recipes can produce independent bundles on the
  same corpus.
- Their differences are documented in skills, visible in telemetry, and
  comparable in eval.

## Phase 6 - Improve render as a product surface

Goal: make rendered wikis useful for reading and critique.

Work items:

- Improve index/navigation for articles and people.
- Make reference blocks easy to scan and jump to.
- Render page metadata cleanly without exposing internal work-state
  noise.
- Preserve citation anchors and backlinks.
- Render broken/missing links gracefully.
- Add a deterministic render smoke test over a fixture bundle.
- Add a visual/manual checklist for the first real site.

Exit criteria:

- A user can open the generated site and understand what the wiki covers,
  navigate pages, inspect references, and spot gaps.
- Render is robust enough to be part of every strategy run.

## Phase 7 - Add graph-RAG and visualization as ablations

Goal: only after the first simple-RAG baseline and eval harness exist,
add retrieval affordances and measure whether they help.

Candidate retrieval additions:

- Personalized PageRank over a seed set.
- Co-citation or bibliographic-coupling traversals.
- Community-aware seeding.
- Optional community summaries only if measured runs show global context
  is a bottleneck.
- Optional lazy per-concept summary/context layers only if writing quality
  is bottlenecked after retrieval improves.

Visualization:

- Prefer a derived/export artifact fed by `events.jsonl`, eval reports,
  and corpus/wiki graph projections.
- A trace visualization should answer concrete strategy questions:
  - breadth vs depth;
  - revisits;
  - dead-end searches;
  - evidence conversion;
  - guided vs scripted behavior.
- Do not block the first wiki on visualization.

Exit criteria:

- Each graph-RAG addition is evaluated against the simple-RAG baseline.
- Strategy changes are ablated one at a time where practical.
- Visualization explains observed metrics; it is not a substitute for
  metrics.

## Deferred

These are valuable, but should not block the first end-to-end wiki:

- figures and equations as first-class citation markers;
- cross-encoder or LLM reranker;
- summary-tier embeddings;
- HippoRAG-style entity binding;
- full GraphRAG-style global indexing;
- trace replay UI beyond the minimum needed for strategy debugging.

## Immediate next ticket

Sharpen `wikify-baseline` and the supporting capability-skill references
so an agent can run:

1. initialize bundle;
2. sample/search corpus;
3. extract concepts;
4. collect evidence;
5. write/check/commit pages;
6. render HTML;
7. eval;
8. close run.

Then execute a smoke-sized simple-RAG wiki on the pinned corpus and fix
only blockers that prevent that first artifact from shipping.
