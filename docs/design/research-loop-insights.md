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

## Concept Graph A/B Test Results

Three modes tested on S5 gap-structured strategy:

| Mode | Composite | Frontier | Chain | Gaps | Refs | Tokens | Time |
|------|-----------|----------|-------|------|------|--------|------|
| No graph (baseline) | **0.632** | **0.967** | 0.383 | **0.700** | 54 | **112K** | **19m** |
| Injected (graph in context) | 0.568 | 0.677 | 0.364 | 0.635 | **86** | 137K | 24m |
| **Tools-only (graph via tools)** | 0.596 | 0.771 | **0.472** | **0.700** | 67 | 133K | 25m |

### Key insight: explicit citation lookup > passive graph

Making the model CALL `find_citation_for()` during writing produces better
chain coherence (0.472 vs 0.383/0.364) because it forces the model to think
about WHY it's citing each paper. Passive injection (graph in system message)
produces more references (86 vs 67) but with shallow integration.

**Default: `inject_concept_graph=False`** (tools-only mode).

### Concept graph lifecycle
- Built during exploration via `record_paper_summary(concept_links=[...])`
- Stored per-session (never in corpus DB)
- Saved to `concept_graph.json` alongside review output
- Reloadable for follow-up outputs (slides, abstracts from same notes)

### Citation lookup tools
- `find_citation_for(claim)`: concept graph first, embedding fallback
- `query_concept_graph(concept)`: neighbors + papers
- `lookup_citation(pattern)`: name/year DB lookup

## Embedding-First Gap Detection (replacing regex/topic approach)

### Before (broken):
- Embedding voids: found nothing (focused corpus has no absolute voids)
- Topical gaps: "Artificial Synapse" vs "Artificial Synapses" (terminology noise)
- Concept links: "authors declare no competing interest" (boilerplate matching)

### After (embedding-first):
- **Coupled-but-divergent gaps**: papers sharing 2+ cited references but with
  conclusion embedding distance > 0.3. Found 17 real gaps.
  Example: "Wang 2022 vs Zhou 2023 (coupling=2, conclusion distance=0.87)"
- **Section-filtered concept links**: only results/discussion/conclusion chunks
  compared, 304 boilerplate chunks excluded, IDF-weighted token labels.
  Found 30 links with scientific content.
- **Science vibes**: paper centroids from only substantive sections (not
  acknowledgments/references/abstract). Used for pair selection.

### Pre-compute cache (built at ingest time):
| Artifact | Size | Load time |
|----------|------|-----------|
| Science vibes | 206 papers | <0.1s |
| Boilerplate IDs | 304 chunk IDs | <0.1s |
| Divergent gaps | 17 pairs | <0.1s |
| Concept links | 30 links | <0.1s |
| KMeans centroids | 12 clusters | <0.1s |
| Topic embeddings | 56 topics | <0.1s |

Total runtime for find_corpus_gaps: 26s -> 0.6s.
