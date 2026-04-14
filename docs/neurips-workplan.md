# NeurIPS workplan: traceable knowledge synthesis

## Working title

**"Traceable Knowledge Synthesis: Provenance Chains for Faithful
Corpus-Scale Wikification"**

## Thesis (falsifiable)

An automated knowledge-synthesis pipeline that maintains an auditable
evidence chain — corpus chunk to extracted quote to inline citation
marker to rendered claim — produces wikis that are **more faithful**
(fewer hallucinated claims), **more verifiable** (human reviewers can
check claims faster and more accurately), and **more structurally
coherent** (the evidence graph predicts topical organisation) than
matched baselines that generate at the same cost without such chains.

The null hypothesis is that the provenance machinery is dead weight:
a simpler retrieve-then-summarise system at equal token budget produces
output of comparable faithfulness and utility, and the evidence chain
adds complexity without measurable benefit.

## Why this is a NeurIPS contribution

1. **Attribution is an open problem.** Post-hoc citation (retrieve a
   passage after generation) is the current standard. wikify does
   inline-constructive attribution: the model is *required* to ground
   every factual sentence at generation time, with a verbatim quote
   that is validated against the source chunk. The architectural
   difference has not been empirically compared.
2. **Long-form multi-document synthesis is under-studied.** Most
   attribution benchmarks evaluate single-query QA. Generating an
   entire encyclopedia from hundreds of documents is a harder,
   less-explored setting where attribution failures compound.
3. **The evidence graph is a novel diagnostic.** The co-evidence graph
   (two pages share a source chunk) emerges from provenance metadata
   and predicts topical structure without any embedding. This is a new
   artefact that baselines cannot produce.

## Study design

### Conditions (independent variable)

Five conditions, all run at three budget levels (0.5x, 1x, 2x of a
reference budget calibrated so the M-strategy produces ~50 pages at 1x),
each with 3 seeds. Total: 5 x 3 x 3 = 45 runs per corpus.

| # | Condition | Description | Provenance | Exploration |
|---|-----------|-------------|------------|-------------|
| B1 | **Retrieve-summarise** | Top-k retrieval per topic (topics from ingest vocabulary), one summarisation call per topic. No exploration, no evidence markers, no quote validation. Standard RAG-then-summarise. | none | none |
| B2 | **Retrieve-summarise + post-hoc cite** | Same as B1, but a second pass appends document-level citations to each paragraph using a citation model (retrieve the most similar source doc for each claim). No quote, no chunk-level tracing. | doc-level, post-hoc | none |
| W1 | **wikify M-strategy, evidence off** | Full Levy exploration (M-cell: `similarity_walk`, `coverage_gap`, `jump_rate=0.1`, adaptive budget) but the writer is instructed to produce prose *without* `[^eN]` markers. No quote validation. Same model tiers, same budget. | none | full (M-cell) |
| W2 | **wikify M-strategy, doc-level evidence** | Same exploration as W1, but evidence markers reference the source *document* only, not the specific chunk or quote. Marker format: `[^eN]: doc_id`. | doc-level, constructive | full (M-cell) |
| **W3** | **wikify M-strategy, full provenance** | The system as built: chunk-level evidence with verbatim quote validation, `G2 >= 0.99` gate enforced. | chunk+quote, constructive | full (M-cell) |

**Why these baselines are credible.** B1 is the most common production
pattern (used by Perplexity pages, NotebookLM, etc): retrieve relevant
chunks, summarise. Reviewers will recognise it. B2 adds post-hoc
citation, the approach used by most recent attribution work (ALCE,
HAGRID, etc). W1 isolates the exploration contribution from the
provenance contribution. W2 tests whether chunk-level granularity
matters over doc-level. W3 is the full system.

The comparison is designed so that **B1 vs W1** measures the value of
structured exploration (same provenance = none), **W1 vs W2 vs W3**
measures the value of increasing provenance granularity (same
exploration), and **B2 vs W3** is the headline comparison (standard
practice vs the proposed system).

### Corpora (at least two, three if feasible)

| Corpus | Domain | Size target | Why |
|--------|--------|-------------|-----|
| C1: ALD | Atomic layer deposition (materials science) | 80-120 papers | Home turf. Dense cross-citation, equation-heavy, figure-heavy. |
| C2: NLP | A topical NLP sub-area (e.g. retrieval-augmented generation) | 80-120 papers | Audience-native. Reviewers can judge quality directly. |
| C3: Biomedical (stretch) | A clinical-trial sub-area or drug class | 60-80 papers | Different document structure (clinical language, tables). Shows generality. |

Two corpora is the minimum for the submission. C3 is stretch.

### Observables (dependent variables)

#### Automated (computed per bundle, no human needed)

| Observable | Source | What it measures |
|---|---|---|
| **F(C)** — coverage residual (M1) | Embedding distance, corpus vs wiki | Does the wiki explain the corpus? Primary automated metric. |
| **Q(C)** — modularity on G_evidence (M3) | Evidence overlap graph | Structural coherence. Only computable for conditions with evidence (W2, W3). |
| **G1** — citation density | Page scan | Fraction of factual sentences with a marker. |
| **G2** — quote verification rate | Quote substring match against source chunk | Fraction of markers whose quote resolves. Only meaningful for W3. |
| **H(C)** — hit rate (M5) | Run log | Exploration efficiency. |
| **R_P, R_C** — person/concept recall (GT-P, GT-C) | Metadata + topics | Does the wiki find the right things? |
| **Pages, tokens, wall-clock** | Run log | Cost profile. |

#### Human evaluation (the paper's core contribution)

Two evaluation tasks, run on the output of all five conditions at the
1x budget level, on both C1 and C2.

**Task 1: Faithfulness audit** (per-claim, expert)

- Sample 100 factual claims from each condition's wiki (stratified:
  50 from high-evidence pages, 50 from low-evidence pages).
- For each claim, present the evaluator with:
  - The claim text (with evidence markers stripped for blinding).
  - The cited source passage (for B2/W2/W3: the cited doc/chunk/quote;
    for B1/W1: the top-1 retrieved passage by embedding similarity as
    a post-hoc reference).
  - The full source document (so they can check context).
- Evaluator labels: **supported** / **partially supported** /
  **not supported** / **fabricated** (no plausible source).
- Evaluators: 2 domain experts per corpus (4 total). Inter-annotator
  agreement reported (Cohen's kappa).

**Task 2: Verifiability and utility** (per-page, broader pool)

- Sample 20 pages per condition (stratified by page length and
  evidence count).
- Each page shown to 3 evaluators (can be graduate students, not
  necessarily domain experts).
- Evaluator rates on 5-point Likert scales:
  - **Verifiability**: "I can check whether the claims in this page are
    true" (1=impossible, 5=trivial).
  - **Trust**: "I trust the factual claims in this page" (1=not at all,
    5=completely).
  - **Utility**: "This page would be useful as a reference for
    someone studying this topic" (1=useless, 5=very useful).
  - **Coherence**: "This page reads as a well-organised article"
    (1=incoherent, 5=excellent).
- Time-to-verify: for a random subset (5 pages per condition),
  evaluator is asked to verify 3 specific claims and we measure
  wall-clock time. Hypothesis: provenance reduces verification time.

**Blinding.** All conditions presented in random order, stripped of
system-specific formatting. Evidence markers in W2/W3 are replaced
with generic footnote numbers. The evaluator sees the footnote content
(source reference) but not which system produced it.

### Hypotheses and statistical tests

| # | Hypothesis | Test | Conditions compared |
|---|-----------|------|---------------------|
| H1 | Full provenance (W3) has higher faithfulness than no provenance (B1, W1) | Two-proportion z-test on supported-claim rate | W3 vs B1, W3 vs W1 |
| H2 | Chunk-level provenance (W3) has higher faithfulness than doc-level (B2, W2) | Two-proportion z-test | W3 vs B2, W3 vs W2 |
| H3 | Provenance reduces human verification time | Paired t-test on log(time) | W3 vs B1 |
| H4 | Provenance increases perceived verifiability and trust | Mann-Whitney U on Likert scores | W3 vs B1, W3 vs W1 |
| H5 | Exploration (W1) improves coverage over static retrieval (B1) at equal cost | Welch t-test on F(C) | W1 vs B1 |
| H6 | G_evidence modularity (W3) predicts human-rated coherence | Spearman rank correlation | W3 pages (varying Q contribution) |

Multiple comparisons corrected with Holm-Bonferroni.

### What would falsify the thesis

- If B1 or B2 achieves comparable supported-claim rates to W3,
  provenance is not pulling its weight on faithfulness.
- If verification time is similar across conditions, the chain is not
  helping humans either.
- If W1 (exploration, no provenance) matches W3 on faithfulness, the
  exploration is doing the work, not the evidence chain.
- If G_evidence modularity does not correlate with human-rated
  coherence, the evidence graph is a diagnostic curiosity, not a
  meaningful signal.

Any of these outcomes is a publishable finding (negative results about
attribution are valuable), but the paper's narrative changes.

## Baselines: implementation plan

### B1 — Retrieve-summarise

1. Use the same ingest pipeline to build the corpus (identical chunks
   and embeddings).
2. Seed topics from the ingest topic vocabulary (same as GT-C source).
3. For each topic: retrieve top-k chunks by embedding similarity
   (k = 20, same embedding model as wikify).
4. Single LLM call per topic: "Write an encyclopedic article about
   {topic} based on the following passages." Same model tier as W3's
   writer (M-tier) for cost parity.
5. No evidence markers, no quote validation.

This is a clean, recognised baseline. It is what most people would
build first.

### B2 — Retrieve-summarise + post-hoc cite

1. Same as B1, producing raw pages.
2. Second pass: for each paragraph, retrieve the most similar source
   document by embedding cosine over paragraph text.
3. Append a document-level citation: `[N] Author et al., Year`.
4. No chunk-level tracing, no verbatim quote.

This matches the ALCE/HAGRID post-hoc attribution paradigm.

### W1, W2 — wikify ablations

These run the full M-strategy pipeline with modified writer prompts:
- W1: writer prompt omits the evidence-marker instruction entirely.
- W2: writer prompt requires `[^eN]` markers but the evidence schema
  only carries `doc_id` (no `chunk_id`, no `quote`).

Same exploration, same budget, same tiers. The only difference is what
the writer is asked to produce and what gets validated.

## Draft material plan

Before human evaluation, we need polished output from all conditions.
Sequence:

### Phase 1: Corpus preparation (week 1-2)

- [ ] Assemble C1 (ALD, ~100 papers) — already available.
- [ ] Assemble C2 (NLP/RAG, ~100 papers) — curate from Semantic Scholar.
- [ ] Run ingest on both corpora. Verify topic vocabulary, citation
      graph, and figure extraction.

### Phase 2: Baseline implementation (week 2-3)

- [ ] Implement B1 (retrieve-summarise) as a standalone script using
      the same corpus store and embedding infrastructure.
- [ ] Implement B2 (post-hoc citation pass) on top of B1 output.
- [ ] Implement W1 and W2 as writer-prompt variants in the existing
      pipeline (feature flag on the writer, not a new pipeline).

### Phase 3: Full runs (week 3-5)

- [ ] Run all 5 conditions x 3 budgets x 3 seeds on C1 (= 45 runs).
- [ ] Run all 5 conditions x 3 budgets x 3 seeds on C2 (= 45 runs).
- [ ] Compute all automated metrics. Generate the curve plots
      (F(C), Q(C), H(C) vs cost).
- [ ] Render HTML for all bundles. Visual QA on a sample.

### Phase 4: Human evaluation (week 5-7)

- [ ] Prepare the evaluation interface (claim sampling, blinding,
      randomisation, timing).
- [ ] Recruit evaluators: 2 domain experts per corpus for Task 1,
      6+ graduate students for Task 2.
- [ ] Run Task 1 (faithfulness audit) and Task 2 (verifiability/utility).
- [ ] Compute inter-annotator agreement, run statistical tests.

### Phase 5: Paper writing (week 7-9)

- [ ] Introduction: the attribution gap in long-form synthesis.
- [ ] System description: the evidence chain architecture (keep brief,
      this is an empirical paper, not a systems paper).
- [ ] Experimental setup: corpora, conditions, metrics, human eval
      protocol.
- [ ] Results: automated metrics + human evaluation + falsification
      outcomes.
- [ ] Analysis: when does provenance help? When doesn't it? What does
      the evidence graph reveal?
- [ ] Related work: ALCE, HAGRID, RARR, SAFE, attributed QA, RAG
      surveys.

## Key risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| B1 performs surprisingly well | Weakens the thesis | Publishable negative result. Pivot narrative to "when does provenance matter?" |
| Human evaluation is noisy | Low statistical power | Pre-register sample sizes. Use paired designs where possible. Pilot on 10 claims first. |
| Cost of 90 runs is high | Budget | Cache sharing across conditions (same corpus, same embedding). B1/B2 are cheap (no exploration). W1/W2 share extract cache with W3. |
| Only 2 corpora limits generality | Reviewer objection | Acknowledge. Frame C1 and C2 as complementary (familiar-to-author vs familiar-to-reviewer). |
| NeurIPS submission deadline | 9-week plan is tight | Phase 1 can start immediately (C1 exists). Baselines are simple. Human eval is the bottleneck — start recruiting in week 1. |

## Scope boundaries

**In scope for this paper:**
- The five conditions above, automated metrics, human evaluation.
- The evidence graph (G_evidence) as a diagnostic artefact.
- Cost-quality analysis across conditions.

**Out of scope (future work):**
- The full E/M/X/agent strategy comparison (that is a separate paper
  about exploration, not attribution).
- Per-axis ablations of the explorer.
- The guided (model-driven) mode.
- Vision-on-demand for figures.
- Iterative refinement (create/refine/merge epochs).

## Target venue and timeline

- **Venue**: NeurIPS 2026 (datasets and benchmarks track is also an
  option if the evaluation protocol becomes the main contribution).
- **Submission deadline**: typically late May 2026.
- **Today**: 2026-04-12. That gives ~6 weeks, which is tight but
  feasible if C1 is ready and baselines are simple.
- **Fallback**: EMNLP 2026 (deadline typically ~June) or ICLR 2027.
