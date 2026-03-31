# Research Loop Insights

Findings from 9 strategy variants tested on a 206-paper ALD memristor corpus.

## Strategy Benchmark Results

| Strategy | Composite | Frontier | Bridge | Gaps | Chain | Time | Tokens |
|----------|-----------|----------|--------|------|-------|------|--------|
| random_walk | **0.489** | **0.910** | 9% | 0.008 | **0.515** | 16m | 85k |
| **enhanced_hybrid** | **0.459** | 0.738 | **15%** | 0.007 | 0.418 | **4.4m** | 101k |
| gap_aware | 0.447 | 0.767 | 8% | **0.012** | 0.460 | 4.5m | 66k |
| hybrid | 0.445 | 0.587 | **17%** | 0.004 | 0.446 | 4.75m | 91k |
| frontier | 0.428 | 0.711 | 11% | 0.007 | 0.418 | 6m | 76k |
| greedy_v2 | 0.420 | 0.605 | 5% | 0.000 | 0.542 | 3.4m | 110k |
| combined | 0.410 | 0.651 | 5% | 0.010 | 0.467 | 3m | 117k |
| abstract_hybrid | 0.362 | 0.380 | 6% | 0.006 | 0.349 | 14m | 111k |
| snowball_v4 | 0.347 | 0.269 | 8% | 0.012 | 0.388 | 24m | 104k |

## Key Findings

### 1. Scanning all abstracts hurts quality
abstract_hybrid (scanned 206 abstracts) scored lowest composite (0.362)
despite maximum corpus awareness. 400KB of abstracts dilutes context,
pulls centroid toward consensus, and reduces chain coherence. The greedy
and frontier rankings already identify which papers matter.

### 2. Random walk wins composite but is expensive
Random walk's serendipity (concept jumps through search_papers) produces
the highest frontier shift (0.910). But at 16 min, it's 4x slower than
the enhanced hybrid which captures most of the value.

### 3. Bridge papers replace random walks cheaply
For each (seed, frontier) pair, the paper closest to their vibe midpoint
is a natural "stepping stone." These are the same connections random walks
find by accident, computed in milliseconds via vector math.

### 4. Coverage saturates at 3 papers
Greedy submodular shows 90% reading coverage with just 3 papers. The
bottleneck is writing, not reading. All strategies converge to ~60%
writing coverage regardless of how many papers are read.

### 5. Frontier shift is the most discriminating metric
Range: 0.269 (snowball) to 0.910 (random walk). Reviews that explore
edges push toward sparse regions. Reviews that cover everything pull
back toward the dense center.

### 6. Bridge and frontier are inversely correlated
High frontier shift = exploring edges (away from mainstream clusters).
High bridging = connecting mainstream clusters. The enhanced hybrid
balances both by reading seeds, frontiers, AND bridge papers.

## Optimal Strategy: Enhanced Hybrid

```
Phase 1: Greedy seeds (3 papers, coverage backbone)
Phase 2: Frontier papers (5, density-ranked, anti-greedy)
Phase 3: Bridge papers (3, midpoints between seed-frontier pairs)
Phase 4: Serendipity pick (1, most dissimilar to read set)
Phase 5: Gap/synthesis analysis
Phase 6: Write with gap emphasis, cross-paper synthesis
```

All phases precomputed in 7.4s via vector math. No search calls needed.

## Quality Metrics (7 dimensions)

| Metric | What it measures | Range observed |
|--------|-----------------|----------------|
| Frontier shift | Centroid direction toward sparse regions | 0.269-0.910 |
| Bridge vectors | Review chunks connecting dissimilar papers | 0-17% |
| Semantic residual | Synthesis vs summarization (SVD projection) | 0.47-0.50 |
| Gap detection | Embedding voids + gap-claim phrases | 0.000-0.012 |
| Arg. coherence | Consecutive chunk pairs in nearby review positions | 0.349-0.542 |
| Topic coverage | PaperTopic vocabulary in review text | 0.203-0.363 |
| Factual specificity | Numeric values + formulas per 1k words (log-scaled) | 0.782-0.998 |

## What Doesn't Work

- **Iterative coverage loops**: 24 min for 2% coverage gain over greedy (snowball_v4)
- **Abstract scanning**: Dilutes focus, wastes context window
- **Cluster-centroid bridge metric**: Geometrically impossible in homogeneous corpus;
  replaced with paper-level approach
- **Threshold-based cross-ref density**: Saturated at 1.0 for all reviews; dropped
- **gzip-based NCD**: Dominated by size effects; kept but low weight

## PageRank Consideration

Currently PageRank runs on a mixed graph (citations + similarity + coupling).
This blends citation authority with embedding proximity. Better approach:
compute PageRank on CITATIONS ONLY as a pure "academic authority" signal,
orthogonal to the embedding-based greedy/frontier/bridge signals.

Seed selection should combine:
1. #1 PageRank paper (most cited, authoritative anchor)
2. Top 2 greedy coverage papers (excluding the PageRank pick)
This gives 1 authority seed + 2 coverage seeds, orthogonal views.
