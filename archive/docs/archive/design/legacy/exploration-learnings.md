# Exploration Strategy Learnings

Findings from researching optimal graph exploration, information theory, and active learning — applied to the ScholarForge corpus exploration problem.

## The Problem

Given a corpus of N papers (as a citation/similarity graph with embeddings), select a reading order and depth that maximizes semantic coverage of the corpus in the generated review, while minimizing reading cost (tokens consumed by the agent).

## Key Insight: Coverage is Submodular

The semantic coverage function (fraction of corpus chunks within distance threshold of a review chunk) is **submodular**: adding the k-th paper yields diminishing marginal coverage gain. This means:

1. **Greedy is near-optimal**: Always reading the paper with highest marginal coverage gain achieves at least (1-1/e) ~ 63% of the theoretical optimum.
2. **You can stop early**: Once marginal gain drops below a threshold, you are provably close to the best achievable coverage.
3. **Citation count is noise**: A review with 25 refs can match a review with 45 refs in coverage (we measured this: v2=61.1% vs v3=60.5%).

## Strategies Ranked by Expected Value

### Tier 1: Implement Now (highest return per line of code)

**1. Greedy Submodular Selection**
- Algorithm: At each step, pick the unread paper whose addition would most increase coverage
- Implementation: compute_coverage() with each candidate paper's chunks added to the review, pick the one with highest delta
- Cost: O(N) coverage computations per step (expensive but correct)
- Optimization: lazy greedy — cache marginal gains in a max-heap, only recompute when a paper reaches the top. 10-100x faster, same output.

**2. Max-Distance Sampling (k-Center)**
- Algorithm: Pick the unread paper whose vibe vector is farthest from the nearest already-read paper
- Implementation: simple argmax over cosine distances from read-set centroid
- This is what `suggest_next_papers` already approximates with the orthogonality score
- Provides a 2-approximation guarantee for coverage

**3. Personalized PageRank (Random Walk with Restart)**
- Algorithm: Run PageRank with restart probability biased toward already-read papers
- Implementation: one-line change to `nx.pagerank(graph, personalization={pid: 1.0 for pid in read_ids})`
- Effect: ranks unread papers by "reachability-weighted importance" from the read set
- Use for: choosing which graph region to explore next

### Tier 2: Implement After Tier 1

**4. Spectral Clustering + Medoid Sampling**
- Cluster papers by vibe similarity (k-means on centroids)
- Read one representative (medoid) per cluster before reading a second from any cluster
- Guarantees cross-topic coverage before depth in any single topic
- Needs: sklearn KMeans, ~50 lines

**5. UCB1 Bandit over Clusters**
- Treat each cluster as an "arm"; reading a paper from cluster k is a "pull"
- Reward = coverage delta from that paper
- UCB1 score = mean_reward + sqrt(2 * ln(total_pulls) / pulls_k)
- Balances exploitation (clusters that gave high coverage) vs exploration (under-sampled clusters)

### Tier 3: Research-Grade

**6. Thompson Sampling**
- Bayesian variant of the bandit: maintain Beta(alpha, beta) per cluster
- Update after each read based on whether coverage improved above threshold
- More adaptive than UCB1 but needs more iterations to converge

**7. Information-Gain Active Learning**
- Train a lightweight logistic regression on "paper was useful for coverage" labels
- Pick the paper with highest entropy in the classifier's prediction
- Needs ~5 labeled examples to bootstrap; gets better with more data

## What We Measured

| Version | Words | Refs | Coverage | Tokens | Time |
|---------|-------|------|----------|--------|------|
| v1 (BFS snowball) | 5,607 | 27 | ~60%* | 80k | 19m |
| v2 (refined snowball) | 3,102 | 25 | 61.1% | 78k | 10m |
| v3 (expanded) | 5,565 | 45 | 60.5% | 131k | 23m |
| v4 (iterative loop) | 4,383 | ~40 | 60.7% | 104k | 24m |

*Estimated, not measured with stored chunk embeddings.

**Key observations:**
- v2 achieved the same coverage as v3 with 60% fewer tokens
- v3's extra 80% words and 80% more citations added 0% coverage
- v4's iterative loop correctly stopped when coverage plateaued
- The coverage ceiling appears to be ~61% at threshold=0.5

## The Coverage Ceiling

61% coverage at threshold=0.5 may be structural:
- Some corpus chunks are methodology-specific (XRD peak details, exact voltage sweep parameters) that no review would cover
- The threshold of 0.5 cosine distance is aggressive — relaxing to 0.6 would increase apparent coverage
- The review is inherently a compression — 100% coverage would mean reproducing the corpus

## Design for the Optimization Loop

The autonomous loop should test strategies on the efficient frontier (coverage vs. cost):

```
For each strategy variant:
  1. Reset reading log
  2. Run the strategy (agent explores + writes)
  3. Measure: coverage, tokens, time, words, style violations
  4. Plot on the efficient frontier
  5. Identify Pareto-optimal strategies

Strategies to test:
  A. Greedy submodular (pure coverage optimization)
  B. Max-distance (k-center guarantee)
  C. Snowball + orthogonal neighbors (current default)
  D. Spectral cluster + medoid sampling
  E. Hybrid: greedy for first 3 papers, then max-distance for diversity
```

The frontier we want to explore: which strategy gives the best coverage/token ratio?
