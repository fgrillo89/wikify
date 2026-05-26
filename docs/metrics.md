# Wikification metrics

Pricing-normalized tiers are S=1/5, M=3/15, L=15/75 (input/output haiku-equivalent per token) — see `src/wikify/config.py`.

## Framing

A wikification strategy is a function from budget to wiki. We study it by
watching **curves**: intrinsic metrics plotted against cost in haiku-
equivalent tokens. The *shape* of the curve — where it saturates, where it
transitions, where it becomes reproducible — is what tells us a strategy
is working.

The physics analogy is load-bearing: we treat the wiki as a system whose
order parameters can be measured directly. No reference wiki is needed;
the order parameter is the ground truth and its curve reveals the phase
transition from disordered to crystalline.

Reference-based evaluation (human gold, oracle strategy, scripted QA with
a review panel) is kept as an *optional* final validation on one small
corpus, never the core loop.

## Multi-wiki substrate (prerequisite)

Every strategy produces an independent, self-contained wiki bundle:

```
data/wikify_bundles/{strategy}_{budget}_{seed}/
  wiki/articles/*.md
  wiki/people/*.md
  run/state.json       # corpus path, strategy, budget, schema version
  run/events.jsonl     # call telemetry, cli invocations, stage changes
  derived/index.json   # page list projection
  derived/eval.json    # metric values (this doc's outputs)
```

Benchmarking = loading two bundles side by side. No shared state between
runs. This was already implied by the architecture; calling it out
explicitly because every metric below operates on a bundle, independently.

## Cost axis (the x-axis of every plot)

`C = sum_calls tokens_in x price_in(tier) + tokens_out x price_out(tier)`
normalised so haiku = 1. Reported in **haiku-equivalent tokens**. Stable
across pricing changes. Time (wallclock seconds) is reported alongside
but is secondary — token cost is what the study optimises.

Every metric below is a function `f(wiki_bundle) -> scalar`. The study's
primary artifact is the curve `f(bundle(strategy, C, seed))` vs `C`,
averaged over 3 seeds, with a shaded +/-1 sigma band.

## The core metrics

The study tracks four metrics, plus one integrity gate:

- **M1** corpus coverage residual `F(C)` — primary order parameter
- **M2** Heaps exponent `beta(C)` — vocabulary saturation
- **M3** graph crystallinity `Q(C), delta_lambda(C)` — structural order parameter
- **M5** hit rate `H(C)` — efficiency
- **M6** grounding gate — integrity floor (not a metric, a gate)

Plus two corpus-derived references that produce per-bundle scalars:

- **GT-P** person recall against author metadata — exact, free
- **GT-C** concept recall against the cleaned ingest topic vocabulary

Susceptibility (the previous M4) is **out of the core**: it triples cost
and the four order parameters above already separate good strategies
from bad without it. Kept as an optional follow-up study on one corpus
once the primary curves are settled. Each has a clear physical or
information-theoretic interpretation. Each produces a curve whose *shape*
is diagnostic.

### M1 — Corpus coverage residual (free energy)

**Definition.**
For each chunk `c` in the corpus, let `d(c) = 1 - max_{p in wiki} cos(embed(c), embed(body(p)))`
be its residual distance to the nearest wiki page. Define

```
F(wiki) = mean_c d(c)
```

`body(p)` is the **entire prose body** of page `p`, with the YAML
frontmatter, the `## Evidence` footnote block, and any reference or
boilerplate sections stripped. One embedding call per page, cached on
the bundle. No title, no aliases, no link list — only the prose the
page actually states. This is what we want the residual to be measured
against, because it captures what the page *says* about the corpus,
not how the page is *named*.

**Interpretation.** The "free energy" of the corpus given the wiki. If
the wiki perfectly explains the corpus, every chunk has a close wiki page
and `F -> 0`. If the wiki misses topics, the chunks in those topics
contribute a large residual. No reference wiki required; this is purely
a property of `(corpus, wiki_bundle)`.

**Curve shape.** `F(C)` should be monotonically decreasing and eventually
saturate. The saturation floor is the intrinsic limit of the
embedding-based explanation (you can never get to 0 — some chunks are
just noise). The **knee** of the curve is the budget at which the wiki
has absorbed most of the corpus. Strategies are compared by:

- the floor `F_inf` they asymptote to (lower is better),
- the budget `C_half` at which they reach halfway to that floor (lower is
  better),
- the smoothness of the descent (rugged curves = unstable sampling).

**Cost.** Cheap. One embedding per page (reused), one cosine per chunk.
Seconds on a CPU for 1k docs.

**Why it's the headline.** It is the only metric here that directly
answers "does this wiki explain this corpus", deterministically, without
any reference. It is the physical-free-energy analog.

### M2 — Concept saturation (Heaps curve)

**Definition.**
Let `N(C)` be the number of distinct concept pages in a bundle built with
budget `C`. We track the whole trajectory `{(C_i, N_i)}` by running the
same strategy at increasing budgets (or, for strategies that expose
intermediate checkpoints, reading them off a single run).

Fit `N(C) ~ a * C^beta`. Report `beta(C)` as a sliding window.

**Interpretation.** This is Heaps' law for wikis. In a healthy run,
`beta` starts near 1 (every unit of budget finds a new concept) and decays
toward 0 (new budget finds no new concepts because the lexicon has
saturated). The **crossover** from `beta ~ 1` to `beta ~ 0` is the wikification
analog of a vocabulary phase transition.

**Curve shape.** A good strategy shows a clean crossover. A pathological
strategy either:

- never leaves `beta ~ 1` (exploring forever, never consolidating — too
  greedy on breadth),
- collapses to `beta ~ 0` immediately (stuck in one neighbourhood — too
  greedy on depth),
- has a non-monotone `beta` (unstable sampling).

**Cost.** Trivially cheap — counting pages.

**Why.** Gives us a single number per budget (`beta`) that captures the
breadth/depth tradeoff without any reference.

### Defining the wiki graph (prerequisite for M3)

M3's whole signal depends on what graph we compute it on. The wrong
choice would make the order parameter measure cross-linking polish
rather than structural coherence. We need a graph that is:

- **intrinsic to the wiki bundle**: derivable from the page files
  themselves with no external models, no embeddings, and no manual
  thresholds;
- **strategy-fair**: not contaminated by how aggressive a strategy's
  cross-link step is — two strategies with identical extracted concepts
  but different link-step hyperparameters should get the same M3;
- **stable under sparsification**: the choice of how to thin the graph
  should not change the qualitative shape of `Q(C)` and `delta_lambda(C)`.

The natural primitive in a wiki bundle that satisfies all three is
**evidence overlap**. Every page lists the corpus chunks it is anchored
to (via its `[^eN]` markers). Two pages that draw on overlapping
evidence are talking about overlapping corpus material — that is the
definition of "structurally related" we want, and it is independent of
whatever the cross-link step did.

We define **two graphs per bundle**, both reported, with one designated
as the order-parameter graph:

#### G_evidence (primary, the M3 graph)

- **Nodes**: all wiki pages, both `concept` and `person` (cross-kind
  edges are real semantic links: a person who worked on a concept will
  share evidence with that concept's page).
- **Edges**: weighted by **document-level** evidence cosine. For each
  page `p`, let `D(p) = { d : exists chunk c in evidence(p), doc(c) = d }`.
  Edge weight:

  ```
  w(p, q) = |D(p) intersection D(q)| / sqrt(|D(p)| * |D(q)|)
  ```

  This is the cosine of the binary doc-membership vectors. Doc-level,
  not chunk-level, because chunk-level overlap is too sparse on real
  corpora — a page typically cites 5-20 chunks and pairwise chunk-set
  intersections are mostly empty. Doc-level captures the "same
  literature" signal cleanly.
- **Sparsification**: keep the **top-k = 10 strongest edges per node**,
  union (not mutual). No magic threshold. `k = 10` is arbitrary but
  reported as a sensitivity-study knob; the qualitative shape of `Q(C)`
  should not depend on it within `k in [5, 20]`.
- **Self-loops**: no.
- **Direction**: undirected.
- **Isolated nodes**: kept as singletons. Pages that share no
  literature with any other page are real and they should drag
  modularity down — that is the signal.

`G_evidence` is computed from the page files alone, in seconds, with no
embeddings and no cross-link state. Two strategies that produce the
same set of `(page -> evidence chunks)` mappings will produce **exactly
the same** `G_evidence`. That is what makes it strategy-fair.

#### G_links (secondary, diagnostic only)

- **Nodes**: same.
- **Edges**: the explicit `links` lists in each page's frontmatter,
  symmetrised. Unweighted (or weighted by reciprocity).
- **Reported as**: a second `Q(C)` and `delta_lambda(C)` curve, **diagnostic only**,
  not the M3 order parameter.

`G_links` measures the cross-link step's quality, not the wiki's
underlying structure. Comparing `Q(G_evidence)` and `Q(G_links)` for the
same bundle is itself diagnostic:

- `Q(G_evidence)` high, `Q(G_links)` low -> the content is crystalline
  but the strategy's cross-link step is failing to surface it. Fix the
  cross-link step.
- `Q(G_evidence)` low, `Q(G_links)` high -> the strategy is wikifying
  the formatting (writing pages that link to each other) without
  underlying evidence convergence. Suspect.
- both high -> real crystallinity, well surfaced.
- both low -> still in the disordered regime.

This split is the main reason the wiki-graph definition matters: we
want to be able to tell those four cases apart.

### M3 — Graph order parameter (crystallinity)

**Definition.**
On `G_evidence` (defined above), compute the **modularity** `Q` of the
best community partition (deterministic Leiden) and the **spectral gap**
`delta_lambda` of the normalised Laplacian — the gap between the second and third
smallest eigenvalues.

Report `Q(C)` and `delta_lambda(C)` on `G_evidence` (the order parameter), with
the same two metrics on `G_links` reported underneath as diagnostic
overlays.

**Interpretation.** Both are "crystallinity" measures for the wiki graph.

- Modularity near 0 = the wiki graph is structurally random (the pages
  do not organise into coherent domains — the wiki is an amorphous blob).
- Modularity near 0.3-0.7 = the wiki has crystallised into communities
  that correspond to topical domains.
- Large spectral gap = a small number of well-separated communities
  dominate. This is a genuine phase-transition signature: in percolation
  and clustering models the spectral gap opens up exactly when a giant
  ordered component appears.

**Curve shape.** In a healthy run, `Q(C)` and `delta_lambda(C)` rise from ~0 at low
budget, hit a plateau, and stay there. A strategy that never reaches the
plateau is producing a structurally random wiki — lots of pages, no
coherence. The budget at which the plateau starts is the "crystallisation
budget" for that strategy.

**Cost.** Leiden on ~10^3 nodes: milliseconds. Sparse eigendecomposition
for the spectral gap: milliseconds.

**Why.** This is the direct physics analog the user asked for. Two
strategies can produce the same number of pages at the same cost, but
one may be crystalline and the other amorphous. `Q` and `delta_lambda` tell them
apart.

### M5 — Hit rate (efficiency)

**Definition.**
`H(C) = |chunks that appear as evidence in at least one page| / |chunks read by any model during the run|`

A "chunk read" is counted once per chunk even if the strategy reads it
multiple times. Reads via section summaries count as fractional (the
fraction of the section's chunks covered).

**Interpretation.** Not an order parameter — there is no phase
transition to read off its curve. It is the cleanest *efficiency* number
we have: what fraction of the reads the strategy performs actually
contribute to the final wiki? A strategy with `H = 0.8` at cost `C` is
doing essentially the same work at 80% of the cost of one with `H = 0.4`.

**Curve shape.** `H(C)` typically starts high on tiny budgets (every
read is precious and becomes evidence) and decays as the strategy is
forced to cover more ground with diminishing returns. A good strategy
decays slowly; a bad one collapses fast. The *ratio* `H / F` (efficiency
over residual) is a single scalar that captures "bang per read" and is
worth tracking alongside the primary `F(C)` curve.

**Cost.** Trivial — it is accounting over the run log.

**Why keep it.** Two strategies can sit on the same point of the
`(C, F)` frontier while doing completely different amounts of work
internally: one reads 100 chunks and uses 90, the other reads 500 and
uses 90. Cost is the same in tokens but the second is wasting headroom
the first could spend on more pages. `H` surfaces that.

### M6 — Grounding gate (integrity)

**Definition.**

- `G1 = fraction of factual sentences in page bodies with a [^eN] marker`
- `G2 = fraction of [^eN] markers whose (chunk_id, quote) resolves to a real corpus chunk with that substring`

**Interpretation.** Not an order parameter. A **gate**. Any run with
`G1 < 0.9` or `G2 < 0.99` is disqualified from the study regardless of
how good its other curves look. A wiki with hallucinated evidence is not
a wiki.

**Cost.** Trivial — a walk over the page files.

**Why.** Integrity floor. Separated from the quality metrics so it
cannot be traded off.

## Corpus-derived references

Two cheap, deterministic reference signals built from data we already
have at ingest time. Neither requires any LLM call. They are reported as
per-run scalars alongside the M1-M5 curves.

### GT-P — People from bibliography metadata

For any corpus with bibtex / doi / arxiv / orcid, the set of authors
who appear in the corpus is deterministically known: it is the union
of author lists across documents.

Pipeline:

1. Read each document's metadata, collect author entries.
2. Normalise with a deterministic name-folding rule:
   - lowercase, strip punctuation, unicode-fold;
   - collapse `"Last, First M."` and `"First M. Last"` to a single key
     `last_firstinitial` (e.g. `fujishima_a`);
   - merge entries that share the canonical key.
3. The result is `G_people`: a set of canonical person ids with display
   names attached.

**Metric: `R_P = |matched(G_people)| / |G_people|`** — fraction of
canonical authors that have a matching `kind: person` page in the wiki
bundle, where matching is `normalize(page.title) in G_people` OR
`any(normalize(alias) in G_people)`. No embeddings needed for people —
name normalisation is sufficient.

This is a real recall number, deterministic, free, and computable on
arbitrarily large corpora.

### GT-C — Concepts from cleaned ingest topic extraction

The ingest pipeline extracts a topic vocabulary per document and a
corpus-wide topic vocabulary, with author-declared keywords flagged.
The implementation lives in `wikify.ingest.topics`:

- `extract_topics(docs_chunks, declared_per_doc)` returns a
  `TopicVocabulary` with `.topics` (the deduplicated corpus vocabulary)
  and `.declared` (topics that came from explicit "Keywords:" /
  "Index Terms:" sections in documents — high precision).
- Internal deduplication merges plurals, absorbs substrings, and merges
  stem variants.
- The vocabulary is serialised to `corpus/topics.json` via
  `TopicVocabulary.to_dict()` and can be read back at eval time.

We reuse this directly. We do **not** invent a new noun-phrase
extractor. The pipeline for GT-C:

1. Load `corpus/topics.json` (or recompute via `extract_topics` if not
   cached).
2. Sanitise:
   - drop topics with document frequency `> 0.5 * n_docs` (too generic
     to be a wiki concept);
   - drop topics with document frequency `< 3` (too rare to be
     corpus-significant);
   - drop topics that are pure stop-phrases (a tiny hand-curated
     blacklist: "introduction", "results", "discussion", "method",
     "abstract", etc.);
   - re-run deduplication once more on the filtered set, in case the
     filter exposed new merges.
3. Embed each surviving topic phrase once, with the same embedding model
   used for the vector store. Cache the embeddings on disk
   (`data/eval/gt_c_embeddings.npz`) so this is paid once per corpus.
4. **Two reference sets**, both reported:
   - `G_concepts_declared`: only topics from `TopicVocabulary.declared`.
     Higher precision, lower recall — these are things authors themselves
     named as keywords.
   - `G_concepts_all`: declared + inferred, after sanitisation. Lower
     precision, higher recall.

**Metric: `R_C = |matched(G)| / |G|`** for both `G_concepts_declared`
and `G_concepts_all`, with matching defined as:

- `normalize(topic) == normalize(page.title)` OR
  `normalize(topic) in {normalize(a) for a in page.aliases}`, OR
- `cos(embed(topic), embed(body(page))) >= 0.78`

The second clause is what makes the embedding cache worth building: a
strategy can name a concept differently from how the ingest topic
extractor named it, and we still want to count that as a match if the
page is unambiguously about that topic. The threshold `0.78` is
slightly looser than the 0.85 used elsewhere because we are matching a
short phrase against a full page body, which lowers cosine on average.

Reported as **two scalars per bundle**: `R_C_declared` and `R_C_all`.
The first is the high-precision recall (closer to a real ground
truth), the second is the high-recall variant. A strategy that is good
on `R_C_declared` but bad on `R_C_all` is biased toward author-named
concepts; the opposite suggests it is finding things authors didn't
bother to declare.

**Important caveat.** GT-C is built from the same corpus the strategies
read. It is *not* an independent ground truth; it is a deterministic
projection of the corpus's own self-description. Strategies that
happen to use the same topic-extraction prior as ingest get an unfair
edge. We treat `R_C` as **a sanity check on the curves**, not as the
primary signal — the primary signal remains M1's `F(C)`. If `F(C)` says
strategy A wins and `R_C` says strategy B wins, we trust `F`.

## The curves the study reports

For each `(strategy, corpus)`, the study produces four plots, each with
cost (haiku-equivalent tokens) on the x-axis, one line per strategy:

1. `F(C)` — corpus coverage residual (M1) **[primary]**
2. `beta(C)` — Heaps exponent (M2)
3. `Q(C)` and `delta_lambda(C)` — graph crystallinity on `G_evidence` (M3),
   with the same two metrics on `G_links` reported as overlay
4. `H(C)` — hit rate (M5)

Plus per-run scalars (one number per `(strategy, budget)` cell):

- `R_P`         — person recall against bibliography metadata (GT-P)
- `R_C_declared`, `R_C_all` — concept recall against the cleaned ingest
                                topic vocabulary (GT-C)
- `G1`, `G2`    — grounding gate; runs failing the gate are excluded

A strategy is **Pareto-optimal** on `(C, F)` if no other strategy
achieves lower `F` at lower `C`. The Pareto frontier on `(C, F)` is the
headline result. The other curves are diagnostic — they explain *why* a
strategy is on or off the frontier.

Hard gate: `G1 >= 0.9 and G2 >= 0.99` (M6). Runs below are excluded.

## Optional: QA panel (kept for final validation)

Scripted question answering over strategy-wiki bundles, with a human
panel rating the answers, is **kept as an option for a final validation
pass on one corpus**, once the curve-based study has identified 2-3
candidate strategies. It is not part of the core loop.

Protocol when/if we run it:

- fix a set of 20-30 scripted questions per corpus, designed to probe
  both central and niche concepts;
- for each candidate strategy-wiki bundle, a zero-knowledge agent reads
  only that bundle and answers the questions;
- answers are pooled, anonymised, and rated by a small human panel on
  a rubric (correctness, support, coverage).

This validates that low `F` and high `Q` actually correspond to a more
useful wiki. If it disagrees with the curves we learned something deep
about the metrics.

## Open questions (narrowed)

1. **Embedding sensitivity for M1.** Does the choice of embedding model
   materially change the strategy ranking on `F(C)`? Cheap to check —
   recompute `F` with a second embedding and look at rank correlation.

2. **Heaps fitting window for M2.** Pick by eye on the first real run.

3. **`G_evidence` sparsification (M3).** `k = 10` top-k per node is the
   default. Verify `Q(C)` shape is qualitatively stable for
   `k in [5, 20]`. If not, switch to disparity-filter sparsification.

4. **Cosine vs Jaccard in `G_evidence`.** Cosine is the default; less
   punishing on hub pages (foundational concepts, prolific people).

5. **Concept-only and person-only `Q` subgraphs.** Combined graph is
   primary; the two subgraph values are a free diagnostic.

6. **GT-C sanitisation thresholds.** The `> 0.5 * n_docs` (too generic)
   and `< 3` (too rare) cutoffs are starting guesses. Should be tuned
   on the first real corpus by inspecting what gets dropped.

7. **GT-C embedding match threshold.** `0.78` for matching a topic
   phrase against a page body is a guess; should be calibrated on a
   small hand-checked set the first time we run it.

8. **Should GT-C exclude inferred topics entirely?** `R_C_declared`
   may be the only honest GT-C number, and `R_C_all` might be too
   noisy to act on. We will know after the first run.
