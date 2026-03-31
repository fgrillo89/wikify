# Research Loop Insights

Findings from 20+ strategy variants tested on a 206-paper ALD memristor corpus.
Metrics recalibrated with expanded gap regex, log-scaled scoring, and coverage/centroid re-added.

## Strategy Benchmark Results (latest metrics, 9 dimensions)

| Strategy | Composite | Coverage | Frontier | Bridge | Gaps | Chain | Time |
|----------|-----------|----------|----------|--------|------|-------|------|
| **v3c (short dense)** | **0.627** | 61% | 0.899 | 10% | **0.775** | 0.417 | 5.5m |
| random_walk | 0.598 | 56% | **0.910** | 9% | 0.531 | **0.515** | 16m |
| gap_aware | 0.590 | 60% | 0.767 | 8% | 0.673 | 0.460 | 4.5m |
| hybrid | 0.582 | 60% | 0.587 | **17%** | 0.627 | 0.446 | 4.75m |
| v3e (gap-struct) | 0.575 | 59% | 0.870 | 0% | 0.704 | 0.502 | 4m |
| definitive_v2 | 0.575 | 62% | 0.711 | 4% | 0.700 | 0.428 | 5m |
| enhanced_hybrid | 0.559 | 63% | 0.738 | 15% | 0.374 | 0.418 | 4.4m |
| v3b (gap-first) | 0.539 | 59% | 0.748 | 5% | 0.500 | 0.386 | 5m |
| v3a (2 jumps) | 0.537 | 60% | 0.760 | 0% | 0.474 | 0.454 | 4m |
| v3d (2PR+walk) | 0.527 | 61% | 0.402 | 8% | 0.700 | 0.384 | 4m |
| snowball_v4 | 0.508 | 62% | 0.269 | 8% | 0.532 | 0.388 | 24m |
| greedy_v2 | 0.489 | 62% | 0.605 | 5% | 0.000 | 0.542 | 3.4m |

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

### 7. Shorter reviews score higher (v3c discovery)
v3c with a 3000-word hard limit scored 0.627 -- the overall best.
The constraint forced every sentence to earn its place. Quality scales
INVERSELY with length in this corpus. Extra words fill with padding.

### 8. "Every sentence bridges 2+ papers" is too rigid
The v3c constraint worked because it forced density, but it produced
unnatural prose. Better: 30-40% synthesis sentences, 30-40% evidence,
15-25% analysis, 5-10% framing. Every PARAGRAPH needs at least one
synthesis sentence, but not every sentence.

### 9. Gap detection needs expanded regex
The original gap regex caught 2/9 gap sentences. Expanding to 25+
phrases ("no published study," "unexplored," "absent," etc.) plus
log-scaled scoring made gap_aware jump from 0.012 to 0.673.

### 10. Citation-only PageRank is orthogonal to greedy coverage
PageRank on citation edges alone gives a different top paper (Li 2018)
than the mixed graph (Wang 2021). Using 1 PageRank seed + 2 greedy
seeds gives orthogonal coverage of authority + breadth.

## Token Efficiency (planned)

Current cost: 70-90k tokens per review (single-turn peak). Key optimizations:
- Prompt caching (cache_control on system prompt): ~50k saved/run
- Tool result compaction (truncate deep_read after processing): ~200-400k saved
- Read-once-summarize pattern (record_paper_summary tool): ~30k saved
- Total estimated: 60-70% reduction in cumulative token cost

## Optimal Strategy: Gap-Oriented Hybrid (Short Tier)

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
