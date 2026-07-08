# Wikify overview

This is the top of the documentation tree. Read it first. Every other
document under `docs/` expands one part of the picture drawn here.

## What Wikify is

Wikify turns a pile of source documents into an **evidence-grounded
wiki**: a set of encyclopedic pages where every factual claim is backed
by a verbatim quote from one of the sources, and that quote can be
traced back to the exact place it came from.

You give Wikify a folder of files (PDF, DOCX, PPTX, HTML, Markdown). It
parses them, then an AI agent reads them the way a human researcher
would — starting from the most important papers, following ideas across
documents, collecting supporting quotes, and writing a page only once a
topic is well enough understood. The result is a browsable wiki of
articles and short biographies, plus a layer of data tables, all
rendered to a static HTML site.

Two ideas make Wikify different from a chat-over-your-documents tool:

- **Grounding.** A page is never written from the model's memory. Each
  claim carries a citation that points to a quote that really exists in
  the corpus. A fabricated quote fails an automatic check and the page
  is rejected.
- **Coverage.** Wikify does not answer one question and stop. It works
  through the whole corpus, building pages until the set of topics is
  saturated, so the wiki reflects what the documents collectively say.

## Core concepts

These terms appear throughout the docs. Each is defined here on first
use and reused with the same meaning everywhere.

- **Corpus.** The read-only input: your source documents after Wikify
  has parsed them into clean text, split them into chunks, computed
  search embeddings, and extracted a citation/topic graph. A corpus is
  built once and never changed during a run. It lives in its own
  database, separate from any wiki you build from it.

- **Chunk.** One small, addressable passage of a source document (a few
  paragraphs). Chunks are the unit Wikify reads, searches, and cites.
  Every chunk has a stable id so a quote can be tied back to its origin.

- **Bundle.** One wiki-building project. A bundle is a directory that
  holds everything produced while building a wiki from a corpus:
  in-progress notes, drafts, the committed pages, event logs, and the
  rendered site. One corpus can feed many bundles.

- **Wiki.** The finished output inside a bundle: the committed
  encyclopedic pages (articles and biographies) plus the links between
  them. A page only becomes part of the wiki after it passes validation.

- **Concept.** A topic that may become a wiki page — for example
  "Atomic Layer Deposition" or a notable author. A concept is a
  work-in-progress entry; it is not yet a page.

- **Dossier (notebook).** The working file for one concept: the
  collected quotes, the documents seen, aliases, and bookkeeping the
  agent accumulates while researching that concept. When the dossier is
  rich enough, it is written up into a page. "Dossier" and "notebook"
  mean the same thing.

- **Evidence.** A vetted quote attached to a concept's dossier: a
  verbatim substring of a specific chunk, kept with the chunk and
  document ids so it can be turned into a citation. Pages are written
  only from evidence.

- **Maturity.** A score from 0 to 1 that measures how ready a concept's
  dossier is to be written. It combines how many quotes were gathered,
  how many different documents they span, and whether the dossier covers
  the expected kinds of content (definition, mechanism, application, and
  so on). A concept is written only once its maturity crosses a fixed
  threshold (0.70). Maturity sorts concepts into bands — `new`,
  `growing`, `stalled`, `ready`, `parked` — that drive what the agent
  does next.

- **Data artifact.** A table of verifiable numbers harvested from the
  corpus — for example a column of reported values across many papers.
  Data artifacts are a separate layer from the wiki pages (see "The two
  output layers" below).

## How the pieces fit together

```
your files
   |
   v   parse, chunk, embed, build graph
[ Corpus ]   read-only document store
   |
   v   the wikify agent loop (research and write)
[ Bundle ]   dossiers -> evidence -> committed pages + data artifacts
   |
   v   render and evaluate
[ Site + metrics ]   static HTML wiki, quality reports
```

A typical end-to-end use is four commands: build a corpus, start a run,
let the agent build the wiki, then render and evaluate it. The agent
loop in the middle is where the real work happens, and it is described
next.

## The main path: the wikify agent loop

The primary way to build a wiki is the `wikify` skill, an agent that
behaves like a research editor. It does not read every document itself.
Instead it runs a top-tier model as the **editor** and dispatches
cheaper **explorer** subagents to do the chunk-by-chunk reading, then
decides what to write. The editor works in rounds. Each round runs the
same eight steps:

```
SENSE -> DECIDE -> DISPATCH -> CONSOLIDATE -> REASSESS -> [CURATE] -> EMIT -> STOP CHECK
```

### SENSE — read the current state

The editor takes one snapshot of where the build stands: the budget
spent and remaining, how many concepts sit in each maturity band,
per-concept scores, coverage so far, and how many data points have been
harvested. The editor never reads chunk text here; it reads summaries
and scores only.

### DECIDE — choose what to do this round

From the snapshot the editor builds a plan with at most one task per
concept per round. It walks a fixed list of **waves** in priority order
and assigns work:

- **WRITE** — every concept that has reached the `ready` band gets
  written into a page. Writing commits a ready concept before other
  work, so this comes first.
- **REFINE** — a committed page is re-drafted when its live evidence has
  grown past a threshold beyond its write-time snapshot, a newly
  committed data table shares a source document with it, or at least a
  set number of topical-neighbour article/person pages (ones sharing a
  source document) have committed since it was written, so it should now
  cross-reference them. Refining converges: each re-commit records the
  current evidence count, tables, and neighbours as the new baseline.
- **GROW** — concepts in the `growing` band get more research: an
  explorer gathers more evidence for them.
- **BRIDGE** — when the wiki splits into weakly connected clusters, a
  task looks for a concept that connects two of them.
- **SEED** — when there are too few concepts, new ones are discovered
  from the most important documents not yet covered. This is **seeding**:
  reading top-ranked source papers to anchor fresh concepts.
- **PERSON** — biography pages for the researchers the wiki leans on,
  ranked first by how many committed-page source documents each authored,
  then by field-prominence metrics, so source-critical authors with a
  modest h-index still get reviewed. A strict quoted-contribution gate
  decides which actually commit (see "Person pages" below).
- **GAP** — every round, a task scans the corpus passages still not
  covered by any dossier or page and either attaches them to a nearby
  page or proposes a new concept. It also records grounded **literature
  gaps** — open questions, contradictions between sources, and
  understudied areas the passages explicitly state — which, when enough
  accumulate, a final pass synthesises into a committed "Literature Gaps
  and Open Questions" page. This is the main driver of coverage.
- **DATA** — every round, a task harvests verifiable numbers and tables
  into the data layer (see "The two output layers").

### DISPATCH — send out the work

The editor spawns one subagent task per plan entry. Explorer tasks walk
the corpus using a small library of named, depth-bounded search
patterns (P1 through P5): hub expansion from seed documents, walking the
citation graph, semantic-boundary expansion, exact-term sweeps for
acronyms and spelling variants, and the gap-explorer that drives
coverage. Each task returns the candidate quotes it found; the editor
records how much each task cost.

Evidence reaches a dossier by one of two gather paths, and they show up
differently in the cost telemetry. A deterministic gather
(`build-evidence`) collects chunks by structural rules with no
per-chunk model call, so its cost lands on the editor's own tier — a
round dominated by it shows almost no cheap-model usage, which is
expected, not a bug. The alternative fans out cheap per-chunk judge
models when model judgment over each chunk is wanted. Both end in the
same evidence ledger.

### CONSOLIDATE — fold results back in

The editor merges what the tasks returned: promising new-concept
suggestions become concepts (a suggested concept needs at least two
supporting chunks before it is promoted, so the roster does not fill
with empty stubs), evidence is attached to the right dossiers, and any
question a subagent could not resolve on its own is decided here by the
editor rather than guessed by the subagent.

### REASSESS — recompute maturity

The editor recomputes the maturity score for only the concepts that
changed this round. This is cheap and decides which concepts move into
the `ready` band for the next WRITE wave.

### CURATE — tidy the roster (every other round)

Periodically the editor cleans up:

- **Dedup.** Two concepts can describe the same thing under different
  names ("Memristance" vs "Memristor"). The editor surfaces candidate
  duplicate pairs by how much evidence they share, then merges genuine
  duplicates (keeping the broader concept and turning the narrower into
  an alias) or keeps them apart. When unsure it keeps them distinct,
  because a wrong merge loses information and a missed merge is caught
  next time.
- **Park.** Concepts that stalled with too little evidence are set aside
  so they stop blocking progress.

### EMIT — record the round

The editor writes one round-summary event: band counts, metrics,
coverage, which waves and patterns ran, and budget used. This event log
is what makes a run resumable and auditable.

### STOP CHECK — decide whether to continue

The loop stops when the work is **complete**, not when it hits a fixed
coverage number. Completeness means all of: the concept roster is
saturated (no new concepts for two rounds), the write queue is drained
(every `ready` concept has been written), and coverage has plateaued
(it stopped rising meaningfully and no dossier crossed the write
threshold). The run also stops early if it exhausts its budget, hits a
round cap, or simply runs out of useful work to do. Otherwise it starts
the next round at SENSE.

A run can be re-entered later on the same bundle. If the corpus has
gained new documents since last time, the editor detects the change,
absorbs the new documents through a seeding pass, and continues.

## Coverage: why it is governed by completeness

It is tempting to ask Wikify to "cover 90% of the corpus." That target
is structurally impossible and should never be set. A parsed paper is
roughly half non-content chunks — references, figure and table captions,
acknowledgments, appendices, page boilerplate — and Wikify never cites
those as evidence. So the raw fraction of chunks cited
(`chunk_coverage_ratio`) cannot approach 1.0 no matter how long the run
goes.

The meaningful signal is **`addressable_coverage_ratio`**: the fraction
of *content* chunks covered, after the non-content chunks are excluded
from the denominator. The gap-explorer wave pushes this ratio up as a
by-product of its work. But even this is not a stop target. The loop is
governed by **completeness** (saturated roster + drained write queue +
coverage plateau), and `addressable_coverage_ratio` is the number to
read to understand how far coverage got — not a threshold to chase.

## Person pages

Some pages are short biographies of notable authors instead of topic
articles. They are built the same way as articles — gather evidence,
cross a maturity gate, write — but with a stricter, separate rule: a
person is written only with at least three quotes that describe an actual
contribution (proposed, introduced, developed, demonstrated, and so on),
drawn from at least two different documents, with author metadata
present. Thinly-covered authors never reach the threshold and silently
drop out, which is deliberate: it keeps person pages few and reserved
for genuine key figures. Person pages are written as normal encyclopedic
prose; they never invent biographical facts the corpus does not support.

## The two output layers

A bundle produces two distinct kinds of output, and it is important not
to confuse them:

1. **The wiki page graph.** Articles and person pages, connected by
   links. These are the queryable, navigable encyclopedia. They live in
   the wiki store and are reached through the wiki query surface.

2. **The data-artifact layer.** Tables of verifiable numbers harvested
   from the corpus (`kind=data` pages). These are a **separate store**.
   They render and they appear in the site navigation, but they are
   **not nodes in the wiki page graph**: looking one up through the wiki
   query surface correctly returns "not found." Data artifacts are
   queried and rebuilt through the dedicated data surface instead, and
   each table re-derives from a stored specification rather than being a
   hand-edited page. The split is intentional — data tables are computed
   views, not encyclopedia nodes — so a "page not found" from the wiki
   side for a data table is expected, and the right move is to fall back
   to the data surface, not to retry on the wiki side.

## Where to go next

See `docs/README.md` for the full documentation map. The two most
important next reads are `docs/architecture.md` (how the agent, the CLI,
and the on-disk bundle fit together) and the component documents that
expand each stage of the loop above.
