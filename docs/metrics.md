# Metrics

Once a run has produced a wiki, three questions matter: is the wiki any
good, was each page worth writing, and how is the build progressing? Wikify
answers them with three separate machineries.

- **Evaluation metrics** judge the *finished* wiki in a bundle. They are
  computed after the fact by `wikify eval`, never call a model, and never
  change anything. This document covers four of them: **M1**, **M3**,
  **M5**, and **M6**.
- **Maturity scoring** runs *during* the build. It scores each concept's
  dossier to decide when it is ready to be written into a page. It is the
  gate the agent loop checks every round.
- **Per-round build metrics** snapshot the *whole build* at the end of a
  round — coverage, page counts, graph structure, budget — and append the
  line to a time series so progress can be tracked and plotted across
  rounds.

The three are independent. Maturity scoring got a page written; the
per-round metrics track the build filling out round over round; the eval
metrics tell you whether the wiki those pages form holds together.

The implementations are `src/wikify/eval/metrics.py` (eval metrics),
`src/wikify/bundle/work/maturity.py` (maturity), and the `metrics` /
`stats` commands in `src/wikify/cli/run.py` (per-round build metrics).

## Running the evaluation

```
wikify eval --bundle <bundle> [--corpus <corpus>]
```

This writes a JSON report to `<bundle>/derived/eval.json` and prints a
short summary. The report has two tiers:

- **Corpus-free metrics** are computed from the bundle's page files
  alone: M3 (graph structure), M5 (hit rate), page counts, figure
  references, and a telemetry rollup. These are always emitted.
- **Corpus-dependent metrics** — M1 (coverage residual) and M6
  (grounding) — need the original corpus to compare pages against the
  source text. Without `--corpus` they are emitted as `null` and listed
  under `corpus_dependent_unavailable`, so you see the gap instead of a
  fabricated zero.

Every metric is a pure function of the bundle (and, where noted, the
corpus). None of them call a model.

## M1 — Coverage residual

**What it measures.** How much of the corpus the wiki actually explains.

**How.** Recall that a corpus is split into **chunks** — small,
addressable passages of the source documents. Each wiki page has a
**body**: the prose it states, with the YAML header, the evidence
footnotes, and boilerplate stripped out. M1 embeds every chunk and every
page body in the same vector space, then for each chunk finds its nearest
page by cosine similarity and records the distance left over:

```
residual(chunk) = 1 - max_over_pages cos(chunk, page_body)
M1 = mean over all chunks of residual(chunk)
```

A chunk that some page covers well sits close to that page and
contributes almost nothing; a chunk on a topic no page discusses sits far
from every page and contributes a large residual.

**How to read it.** Lower is better. If the wiki perfectly explained the
corpus, M1 would approach 0; in practice it lands around 0.3 to 0.9
because some chunks (references, captions, boilerplate) are noise no page
should cover. A bundle with no pages returns 1.0. The value comes from
the page *body* on purpose — what a page says about the corpus, not how
it is titled.

**Cost.** Cheap: one embedding per page (cached on the bundle), one
cosine per chunk.

## M3 — Graph crystallinity

**What it measures.** Whether the pages organize into coherent topical
clusters, or sit as an unrelated pile.

**How.** M3 builds a graph over the pages and measures how cleanly it
splits into communities. The graph it uses is **G_evidence**, derived
from the pages alone with no model and no embeddings:

- **Nodes** are all pages, both articles and person pages.
- **Edges** connect pages that draw on the same documents. For each page,
  take the set of documents its evidence comes from. Two pages are linked
  with weight

  ```
  w(p, q) = |docs(p) ∩ docs(q)| / sqrt(|docs(p)| * |docs(q)|)
  ```

  This is the cosine of their document-membership vectors. It is
  measured at the document level, not the chunk level, because pairwise
  chunk overlap is too sparse to be a reliable signal. Each node keeps
  its 10 strongest edges (the union across nodes, so the graph stays
  undirected). Pages that share no documents with anyone stay as isolated
  nodes — that is real, and it should pull the score down.

Because G_evidence is built only from which chunks each page cites, two
runs that produce the same page-to-evidence mapping get exactly the same
graph. It does not reward a run for adding decorative cross-links.

On that graph M3 reports two numbers:

- **Modularity (Q)** — from the Louvain community partition. Near 0 means
  the graph is structurally random (an amorphous blob). Roughly 0.3 to
  0.7 means the pages have crystallized into communities that line up
  with topical domains.
- **Spectral gap (Δλ)** — the gap between the second- and third-smallest
  eigenvalues of the normalized graph Laplacian. A larger gap means a
  small number of well-separated communities dominate.

**Diagnostic overlay: G_links.** The report also computes Q and Δλ on
**G_links**, a second graph built from the explicit `links` lists in each
page's header instead of shared evidence. Comparing the two is
informative: high G_evidence Q with low G_links Q means the content is
coherent but the run's cross-linking is failing to surface it; the
reverse means a run is linking pages together without underlying evidence
to back the connection. G_links is diagnostic only — G_evidence is the
metric.

**How to read it.** Higher Q and a wider Δλ mean a better-organized wiki.
A run that produces many pages but near-zero modularity built quantity
without coherence.

**Cost.** Milliseconds: Louvain plus one eigendecomposition on a graph of
~10³ nodes.

## M5 — Hit rate

**What it measures.** Efficiency — what fraction of the reading the agent
did actually ended up in the wiki.

**How.** During a run the agent records every chunk it reads as a
`chunk_read` event in the bundle's event log. M5 intersects that set with
the chunks that became **evidence** on some page:

```
M5 = |chunks read that became evidence| / |chunks read|
```

A chunk read several times counts once.

**How to read it.** Higher is better. M5 = 0.8 means four of every five
chunks the agent read paid off; M5 = 0.4 means more than half the reading
was wasted. Two runs can land at the same token cost while one reads 100
chunks and uses 90 and the other reads 500 and uses 90 — same cost, very
different efficiency, and M5 is what separates them. If the run recorded
no `chunk_read` events, M5 is reported as `null` (no signal) rather than
0.

**Cost.** Trivial — accounting over the event log.

## M6 — Grounding gate

**What it measures.** Integrity: are the pages actually backed by real
quotes from the corpus, or do they make claims out of thin air. This is a
pass/fail gate, not a quality dial — a wiki with invented evidence is not
a wiki regardless of how it scores elsewhere.

**How.** Every factual sentence in a page should sit near a citation
**marker** (`[^e1]`, `[^e2]`, ...), and every marker points at a quote
that is supposed to be a verbatim substring of a specific chunk. M6
checks both halves:

- **G1 (claim coverage).** The fraction of factual sentences that sit
  within 2 sentences of a marker in the same paragraph. The window
  follows the Wikipedia convention that one citation can carry the
  surrounding cluster of claims, so a short paragraph with a single
  marker still counts as fully covered.
- **G2 (evidence resolves).** The fraction of markers whose declared
  quote actually appears in the chunk it cites. This is where a
  fabricated quote is caught: the quote text is looked up in the real
  corpus chunk, and if it is not there the marker fails.

**How to read it.** The gate passes when **G1 ≥ 0.85 and G2 ≥ 0.95**.
G2 is the strict one — markers must resolve to real corpus text almost
without exception. A run that fails the gate is disqualified; its other
metrics do not matter.

**Cost.** Trivial — a walk over the page files plus a lookup of the cited
chunks.

## Maturity scoring

Maturity is a different machine entirely. It runs inside the build loop,
in the REASSESS step of every round, and decides when a concept's
**dossier** (its gathered quotes and bookkeeping) is ready to be written
into a page. It is a pure function of the dossier, the event log, and the
link neighborhood — no model calls.

The score is a number from 0 to 1. A concept is written only when its
score crosses the **threshold of 0.70**. The score also sorts every
concept into a **band** that tells the agent what to do with it next:
`new`, `growing`, `stalled`, or `ready`. Concepts the curator has set
aside or merged carry a terminal band (`parked`, `merged`, `dropped`) and
drop out of the active roster.

### Gates first, then the weighted score

A concept earns a nonzero score only after it clears a set of hard gates.
Miss any gate and the score is 0 (band `new`, `growing`, or `stalled`
depending on whether evidence is still arriving).

For an **article** the gates are:

- the evidence includes a **definition** (a quote that defines the
  topic),
- at least **8 evidence chunks**,
- at least **4 distinct documents**,
- growth has **stalled** — no new evidence was added in the last 2
  rounds.

The stalled-growth gate is deliberate: a page is written when its dossier
has stopped growing, i.e. the topic has been read out, not while quotes
are still pouring in.

Once the gates pass, the score is a weighted sum (maximum 1.0):

| Component | Weight | What it rewards |
|---|---|---|
| `n_chunks` | 0.25 | evidence volume, saturating at 12 chunks |
| `n_docs` | 0.15 | document spread, saturating at 6 documents |
| `kinds_coverage` | 0.30 | how many of the expected content kinds are present |
| `redundancy_inverse` | 0.20 | how little the evidence overlaps a linked page's |
| `diversity_bonus` | 0.10 | how evenly the evidence spreads across documents |

`kinds_coverage` uses a **stencil** per article kind, listing the content
kinds the topic should cover. The content kinds —
definition, mechanism, application, limitation, variant — are detected by
matching regular expressions against the quote text. The four stencils:

- `article-method`: definition, mechanism, application
- `article-theory`: definition, mechanism, limitation
- `article-survey`: definition, variant, application
- `article-history`: definition, variant, limitation

`redundancy_inverse` looks at the chunks the concept's linked neighbors
already cite and penalizes a dossier that just repeats them. The
`diversity_bonus` is `1 - HHI` over the per-document share of evidence, so
it is 0 when every quote comes from one document and approaches 1 when the
quotes spread evenly.

### Person pages score differently

A **person page** uses its own gates and weights, because a biography
needs different evidence than a topic article:

- Gates: at least **3 quotes describing a contribution** (proposed,
  introduced, developed, and so on), drawn from at least **2 distinct
  documents**, with **author metadata present** on the concept.
- Components: contribution quotes (0.45, saturating at 4), document
  spread (0.25, saturating at 3), a collaboration signal (0.15), and a
  temporal anchor such as a year (0.15).

This is why thinly-covered authors never reach the threshold and quietly
drop out — the contribution and document gates keep person pages reserved
for genuine key figures.

### Bands

The band follows from the score and gates:

- **new** — no evidence yet, or score below 0.50.
- **growing** — evidence is still arriving, or the score is between 0.50
  and the 0.70 threshold.
- **stalled** — gates not met and no new evidence is coming in; the
  concept is set aside as unlikely to mature.
- **ready** — gates pass and the score is at or above 0.70; the next
  WRITE wave turns it into a page.

The agent loop reads these bands directly: `ready` concepts get written,
`growing` concepts get more research, `stalled` ones get parked.

## Per-round build metrics

Eval judges the endpoint and maturity judges a single concept's readiness.
Neither tells you whether the build is *making progress*. That is the third
machinery: a per-round snapshot of the whole bundle, appended to a time
series so coverage, page count, and graph structure can be tracked — and
plotted — round over round.

### Snapshotting a round

```
wikify run metrics --run <bundle> --round N [--corpus <corpus>]
```

This computes one snapshot and **appends** it as a single JSON line to
`<bundle>/derived/stats.jsonl`. The record carries:

- `round` — the round number this snapshot describes.
- `n_committed_pages`, `n_articles`, `n_people` — committed page counts.
- `band_counts` — the maturity-band histogram over all concepts, with
  committed concepts folded into a `committed` band.
- `chunk_coverage_ratio`, `addressable_coverage_ratio` — corpus coverage.
- `n_data_points`, `n_data_artifacts` — data-layer claim and table counts.
- `budget_spent_haiku_eq` — cumulative spend so far.
- `M1` — coverage residual, and `M3` — the **G_evidence** modularity, the
  same graph-structure number the eval machinery reports. Both are reused
  from `wikify.eval.metrics`, not reimplemented.

Coverage and M1 need the source text, so `chunk_coverage_ratio`,
`addressable_coverage_ratio`, and `M1` are `null` unless `--corpus` is
passed — the gap is shown, never a fabricated zero. Recording the same
round twice appends a second line; the reader keeps the latest per round.

### Reading the series

```
wikify run stats [--run <bundle>] [--format json|csv] [--plot <out.svg>]
```

`run stats` reads `derived/stats.jsonl`, dedupes to the latest record per
round, and sorts by round. If the file is absent or empty it falls back to
reconstructing a minimal series from `round_completed` events in the event
log. `--format json` (default) prints the record list; `--format csv`
emits a header row (`round, pages, chunk_cov, addr_cov, budget, M1, M3,
n_artifacts`) plus one row per round.

`--plot <out.svg>` writes a chart in addition to the series. The plot is a
**hand-rolled, dependency-free SVG** — two stacked panels, addressable
coverage and cumulative committed pages against round — with no plotting
library involved. The series is still emitted in the requested format; a
trailing JSON status line reports the plot path.

## Data-layer completeness: `data_recall`

The data layer carries its own per-property completeness signal.
`wikify data harvest-property` sweeps every corpus chunk that mentions a
property and reports `data_recall = docs_in_table / docs_mentioning_property`
— the fraction of documents that mention the property whose value actually
made it into a quote-verified table row. A property broadly reported across
the corpus but thinly extracted scores low, and the `--require-recall`
consolidation gate refuses to commit such a table (below 0.75 recall once
at least 10 documents mention the property) unless `--skip-recall` is
passed.
