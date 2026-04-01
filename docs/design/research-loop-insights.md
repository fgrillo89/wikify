# Research Loop Insights

Findings from 20+ strategy variants on a 206-paper ALD memristor corpus.

## Current SOTA: One-Shot with Pre-Computed Context

**Composite 0.615, 58 refs, 15% bridge, 6 min, 58K tokens.**

```
Pre-compute (10s, no LLM):
  frontier_exploration_order → 12 papers (1PR + 2greedy + 5frontier + 3bridge + 1serendipity)
  read_paper_digest × 5 + get_paper × 7 → ~30KB digests
  scan_all_abstracts(50) → top 50 by PageRank, ~30KB
  find_corpus_gaps → coupled-but-divergent pairs (cached, 0.6s)

One-shot write (~5 min, single LLM call):
  System: style guide + writer instructions (~3KB)
  User: digests + abstracts + gaps + citation list (~65KB)
  Output: 4000-5000 word review with [REF:DisplayName] markers
```

## Key Findings (ranked by impact)

1. **Fewer LLM turns = better coherence.** One-shot chain coherence (0.875) crushes multi-turn agent loop (0.472). The agent loop fragments the narrative.
2. **Pre-computation is free.** Frontier order, gaps, digests, abstracts — all computed from stored embeddings in 10s with zero LLM tokens.
3. **Coverage saturates at 3 papers.** Greedy submodular shows 90% reading coverage with 3 papers. Reading more doesn't help writing coverage (~60% ceiling).
4. **Shorter reviews score higher.** 3000-word hard limit forced density. Quality scales inversely with length.
5. **Citation-only PageRank is orthogonal to greedy coverage.** 1 PageRank seed + 2 greedy seeds = different papers for different reasons.
6. **Explicit citation lookup > passive injection.** Tools-only (find_citation_for) beats graph-in-context on chain coherence. Making the model work for citations improves them.
7. **Bridge papers replace random walks.** Vibe midpoints between seed-frontier pairs found in milliseconds vs 16 min random walk.

## Gap Detection: Embedding-First

- **Coupled-but-divergent pairs**: papers sharing citations but diverging in conclusions. 17 real gaps found (vs previous terminology noise).
- **Section-filtered concept links**: results/discussion/conclusion only, 304 boilerplate chunks excluded, IDF-weighted labels. 30 links.
- **Science vibes**: centroids from substantive sections only (not acknowledgments/references).
- All cached at ingest time. Runtime: 0.6s.

## Quality Metrics (9 dimensions)

| Metric | What it measures | Weight |
|--------|-----------------|--------|
| Semantic coverage | Corpus chunks covered by review | 0.10 |
| Centroid alignment | Review center vs corpus center | 0.08 |
| Frontier shift | Push toward sparse regions | 0.14 |
| Bridge vectors | Chunks connecting dissimilar papers | 0.10 |
| Semantic residual | Synthesis vs summarization (SVD) | 0.10 |
| Gap detection | Embedding voids + gap-claim phrases | 0.14 |
| Arg. coherence | Consecutive chunk pairs preserved | 0.12 |
| Topic coverage | PaperTopic vocabulary in review | 0.10 |
| Factual specificity | Numbers + formulas per 1k words | 0.12 |

## What Doesn't Work

- Iterative coverage loops (24 min for 2% gain)
- Abstract scanning (400KB dilutes context)
- Injecting concept graph passively (more refs but shallower)
- Topical gap detection from topic strings (finds terminology, not gaps)
- Chunk similarity without section filtering (finds boilerplate)
