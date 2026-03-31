# Knowledge Graph Exploration Strategies

Research synthesis for maximizing information coverage while minimizing reading cost.
Grounded in the ScholarForge infrastructure: NetworkX graph (citation + similarity +
coupling edges), ChromaDB 384-dim embeddings (all-MiniLM-L6-v2), SQLite paper store,
and the existing `GraphMetrics` (PageRank, betweenness, degree centrality).

The **baseline** to beat is Snowball: BFS from the top-PageRank paper, following
citation/similarity edges until the token budget runs out. It is cheap and
deterministic but has two failure modes: (1) it clusters around one region of the
graph, and (2) it has no mechanism to detect when new papers stop adding information.

---

## 1. Multi-Armed Bandit: UCB1 and Thompson Sampling

### Framing

- **Arms**: paper clusters (UCB1 works best at the cluster level, where each arm can
  be pulled multiple times as new papers from the cluster are read; applying it to
  individual papers degenerates to a one-shot problem since each paper is read 0 or 1
  times — use clusters as arms and treat each read as a pull from that cluster's arm)
- **Pull**: read a paper from that cluster (costs ~N tokens)
- **Reward**: information gain — how much the paper's embedding shifts the centroid of
  the "read so far" set. Concretely: `cosine_distance(paper_vibe, mean(read_vibes))`
  — large distance means high reward (new ground), small distance means redundant.
- **Goal**: maximize total reward within token budget B.

### UCB1

```
Inputs:
  papers: list of Paper, each with embedding vibe (384-dim)
  budget: int  # tokens
  c: float = sqrt(2)  # exploration constant

State:
  read_vibes = []            # embeddings of papers read so far
  n_pulled[p] = 0            # times paper p has been read (0 or 1 here)
  reward[p] = 0.0            # observed information gain for paper p
  t = 0                      # total reads so far

Algorithm:
  # Phase 0: read top-3 PageRank papers to initialize (cold-start)
  for p in hub_papers[:3]:
      vibe = get_vibe(p)
      read_vibes.append(vibe)
      reward[p] = 1.0        # assume high value for hubs
      n_pulled[p] = 1
      t += 1

  while tokens_remaining > 0:
      mean_vibe = mean(read_vibes)

      # UCB score for each unread paper
      for p in unread_papers:
          if n_pulled[p] == 0:
              ucb[p] = inf   # force exploration of unread papers
          else:
              ucb[p] = reward[p] + c * sqrt(log(t) / n_pulled[p])

      # Select paper with highest UCB score
      p_star = argmax(ucb)
      read(p_star)

      # Update state
      new_vibe = get_vibe(p_star)
      gain = cosine_distance(new_vibe, mean_vibe)
      reward[p_star] = gain
      read_vibes.append(new_vibe)
      n_pulled[p_star] = 1
      t += 1
      tokens_remaining -= token_count(p_star)
```

### Thompson Sampling

More Bayesian: maintain a Beta(alpha, beta) prior per paper representing belief that
reading it will yield high information gain. Sample from the posterior each round.

```
Inputs:
  papers, budget
  # Prior: Beta(1, 1) = uniform belief about each paper's value

State:
  alpha[p] = 1.0 for all p   # successes (high-gain reads)
  beta[p]  = 1.0 for all p   # failures  (low-gain reads)
  threshold = 0.3             # cosine distance above which a read "succeeds"

Algorithm:
  for p in hub_papers[:3]:   # cold start
      alpha[p] += 2.0        # boost hubs
      read_vibes.append(get_vibe(p))

  while tokens_remaining > 0:
      mean_vibe = mean(read_vibes)

      # Sample from each paper's posterior
      samples = {p: Beta(alpha[p], beta[p]).sample() for p in unread_papers}
      p_star = argmax(samples)

      read(p_star)
      gain = cosine_distance(get_vibe(p_star), mean_vibe)

      if gain >= threshold:
          alpha[p_star] += 1.0   # success: high info gain
      else:
          beta[p_star]  += 1.0   # failure: redundant

      read_vibes.append(get_vibe(p_star))
      tokens_remaining -= token_count(p_star)
```

### Mapping to ScholarForge

- `get_vibe(p)` already exists: `compute_paper_vibes()` in `evaluate/coverage.py`
  returns a 384-dim centroid per paper.
- `mean(read_vibes)` is the current "knowledge state" centroid.
- Reward is `1 - cosine_similarity(paper_vibe, knowledge_centroid)` — a number in [0,1].
- The reading log (`agent/reading_log.py`) tracks what has been read; this maps to the
  bandit's "pulled arms" state.

### When does it beat Snowball?

- **Heterogeneous corpus**: when papers cluster into 3+ distant semantic communities,
  Snowball exhausts one community and runs out of budget before visiting others. UCB1
  forces exploration of distant regions proportional to log(t).
- **Redundant dense clusters**: Thompson sampling quickly assigns high beta (failure)
  to papers in a saturated cluster and stops visiting them.
- Snowball wins on **star-shaped corpora** where one hub paper genuinely dominates and
  everything downstream is relevant.

### Implementation complexity

No training required. Needs only:
1. `get_vibe()` per paper — already implemented.
2. `cosine_distance()` — standard numpy/scipy.
3. A dict tracking `alpha/beta` or `(reward, n_pulled)` per paper ID.

Estimated implementation: ~100 lines in `retrieve/strategies/bandit.py`.

---

## 2. Graph Exploration: Spectral Clustering + Medoid Sampling

### Core idea

The corpus graph has latent community structure: papers on ALD nucleation cluster
separately from papers on film uniformity or reactor design. Spectral clustering
identifies these communities from the graph Laplacian. Then sample one representative
(medoid) per community before exhausting any single community.

### Spectral Clustering + Medoid Sampling

```
Inputs:
  G: NetworkX graph (already built by build_corpus_graph())
  k: int  # number of clusters (estimate: sqrt(N) or set manually)
  budget: int

Algorithm:
  # Step 1: Build Laplacian
  A = adjacency_matrix(G, weight='weight')
  D = degree_matrix(A)
  L_norm = D^{-1/2} (D - A) D^{-1/2}   # normalized Laplacian

  # Step 2: Spectral embedding — top-k eigenvectors of L_norm
  eigenvalues, eigenvectors = eig(L_norm, k=k)
  # Each paper maps to a k-dim vector (its row in eigenvectors)
  embeddings = normalize(eigenvectors)   # L2-normalize rows

  # Step 3: K-means on spectral embeddings
  labels = kmeans(embeddings, k=k)

  # Step 4: Select medoid per cluster
  medoids = []
  for cluster_id in range(k):
      cluster_papers = [p for p in papers if labels[p] == cluster_id]
      cluster_embs   = [embeddings[p] for p in cluster_papers]
      centroid = mean(cluster_embs)
      medoid = argmin(cosine_distance(emb, centroid) for emb in cluster_embs)
      medoids.append(cluster_papers[medoid])

  # Step 5: Read medoids first (guaranteed cross-cluster coverage)
  read_order = medoids

  # Step 6: Fill remaining budget with intra-cluster BFS from each medoid
  for medoid in medoids:
      cluster_papers = sorted_by_pagerank(papers in medoid's cluster)
      read_order.extend(cluster_papers not already in read_order)

  # Step 7: Read in order until budget exhausted
  for p in read_order:
      if tokens_remaining <= 0: break
      read(p)
      tokens_remaining -= token_count(p)
```

### Random Walk with Restart (RWR)

RWR is a simpler alternative to spectral clustering. It computes, for each node, its
"relevance" to a set of seed papers by simulating random walks that restart at the
seeds with probability alpha.

```
Inputs:
  G: graph, seeds: list of paper IDs (e.g. hub papers), alpha=0.15

Algorithm:
  r = zeros(N)
  r[seeds] = 1.0 / len(seeds)   # initial distribution

  for _ in range(max_iter=50):
      r_new = (1 - alpha) * A_normalized @ r + alpha * seed_distribution
      if ||r_new - r|| < tol: break
      r = r_new

  # Papers ranked by r[p] are most relevant to the seed set
  read in descending order of r until budget exhausted
```

RWR is essentially PageRank personalized to your seed papers. The `nx.pagerank()`
call in `graph/metrics.py` already accepts a `personalization` dict — this is a
one-liner change.

### Mapping to ScholarForge

- Spectral clustering: use `scipy.sparse.linalg.eigsh` on the graph Laplacian.
  The graph is already a NetworkX DiGraph; `nx.normalized_laplacian_matrix()` gives
  you L directly.
- Alternatively, skip the graph entirely and cluster on **ChromaDB embeddings**
  (already 384-dim). Use `sklearn.cluster.KMeans` or `hdbscan` on paper vibes.
  This is simpler and may give better semantic clusters than the citation graph alone.
- RWR: change `nx.pagerank(graph, personalization={hub_id: 1.0})` in `compute_metrics()`.

### When does it beat Snowball?

- **Modular corpora**: when the graph has clear community structure (e.g., your ALD
  corpus spanning nucleation, growth kinetics, and characterization subfields). Snowball
  follows edges and gets trapped in one module. Spectral clustering guarantees you visit
  each module.
- **Small, dense corpora** (< 200 papers): spectral clustering is cheap and the coverage
  gain is high.
- Snowball wins when the corpus is already well-connected with no module structure.

### Implementation complexity

- Spectral clustering on embeddings: `sklearn` only, ~50 lines.
- Spectral clustering on graph Laplacian: `scipy.sparse` + `sklearn`, ~80 lines.
- RWR: one-liner using existing `nx.pagerank()`.

---

## 3. Active Learning: Maximum Entropy / Maximum Information Gain

### Core idea

At each step, pick the paper whose content is most **surprising** given what you have
already read. Surprise = distance from the convex hull of already-read embeddings.
This is equivalent to maximum entropy sampling in embedding space.

### Maximum Distance Sampling (greedy)

```
Inputs:
  papers: list, each with vibe v_p (384-dim)
  budget: int

State:
  S = []              # set of papers read so far
  S_vibes = []        # their embeddings

Algorithm:
  # Seed with the top-PageRank paper
  seed = hub_papers[0]
  read(seed)
  S.append(seed); S_vibes.append(get_vibe(seed))

  while tokens_remaining > 0:
      # Score each unread paper by its distance to the NEAREST already-read paper
      # (max-min distance = furthest from any paper we've seen)
      for p in unread_papers:
          v_p = get_vibe(p)
          dist[p] = min(cosine_distance(v_p, v_s) for v_s in S_vibes)
          # Low dist = redundant. High dist = new territory.

      p_star = argmax(dist)
      read(p_star)
      S.append(p_star); S_vibes.append(get_vibe(p_star))
      tokens_remaining -= token_count(p_star)
```

This is the **k-center** problem: pick papers that maximize the minimum distance to
the nearest already-read paper. The greedy solution is a 2-approximation.

### Information Gain Variant (model-based)

Train a lightweight logistic regression on "relevant/not relevant" labels (the agent
can self-label based on whether it cited the paper in a draft). Then pick the paper
with maximum entropy H(p) = -p*log(p) - (1-p)*log(1-p) where p is the predicted
relevance probability.

```
State:
  labeled_papers = {paper_id: relevant (1) or not (0)}  # from reading log
  model = LogisticRegression()

Algorithm:
  # After reading N >= 5 papers:
  model.fit(X=[get_vibe(p) for p in labeled_papers],
            y=[label for p in labeled_papers])

  for p in unread_papers:
      prob = model.predict_proba([get_vibe(p)])[0]
      entropy[p] = -sum(q * log(q) for q in prob)

  # Max-entropy paper: model is most uncertain about it
  p_star = argmax(entropy)
  read(p_star)
```

### Mapping to ScholarForge

- `get_vibe()` returns the 384-dim paper centroid — plug directly into `dist` computation.
- The reading log already tracks which papers were read. "Cited in draft" can be
  approximated by whether the paper appears in the references section the agent wrote.
- `suggest_next_papers()` in `agent/tools.py` already uses a `0.7 * orthogonality +
  0.3 * graph_proximity` score — this is approximately maximum-distance sampling with a
  graph regularizer. The active learning framing gives a cleaner theoretical justification
  and replaces the hand-tuned `0.7/0.3` split with a principled criterion.

### When does it beat Snowball?

- **Wide, flat corpora** with many weakly-connected papers. Snowball follows edges and
  misses isolated clusters. Max-distance sampling in embedding space reaches them even
  if there are no citation links.
- **Diminishing-returns detection**: once all `dist[p]` values fall below a threshold,
  you know the corpus is semantically exhausted. Snowball has no such signal.
- Snowball wins when the corpus is strongly connected and edge-following is a good
  proxy for semantic similarity.

### Implementation complexity

- Max-distance sampling: ~40 lines, no training, no new dependencies.
- Logistic regression variant: `sklearn`, ~30 extra lines, needs labeled data (5+
  reads before it's useful).

---

## 4. Optimal Stopping: When to Stop Reading and Start Writing

### The Secretary Problem Analogy

You are reviewing N papers sequentially. After reading each paper, decide: start
writing now (commit) or keep reading. You cannot un-commit. The goal is to maximize
the quality of the final paper given a reading budget.

The classic **1/e rule**: reject the first N/e papers (pure exploration), then commit
to the next paper that is better than all previously seen. In our setting:

- "Better" = adds more coverage than any paper read during exploration.
- N = total corpus size.
- N/e ≈ 37% of the corpus is the exploration phase.

This gives the optimal stopping rule when papers arrive in random order. But our
setting is not random — we can actively choose which paper to read next. That breaks
the classic proof and makes it more favorable.

### Explore-Then-Commit with Coverage Signal

```
Inputs:
  budget: int, corpus_size: N
  gamma: float = 0.37    # exploration fraction (1/e by default)
  delta_threshold: float = 0.02   # minimum coverage gain to keep reading

State:
  coverage_history = []   # coverage score after each read
  phase = "explore"

Algorithm:
  explore_budget = int(gamma * N)   # papers to read before committing
  exploration_reads = 0

  # Phase 1: Explore (read without writing)
  while exploration_reads < explore_budget and tokens_remaining > 0:
      p = select_next_paper(strategy=max_distance)
      read(p)
      exploration_reads += 1

  # After exploration: write an initial draft
  write_draft()
  coverage = compute_coverage(draft, corpus)
  coverage_history.append(coverage)

  # Phase 2: Commit — keep revising only if coverage is still improving
  while tokens_remaining > 0:
      p = select_next_paper(strategy=max_coverage_gap)
      read(p)
      revise_draft()
      new_coverage = compute_coverage(draft, corpus)
      delta = new_coverage - coverage_history[-1]
      coverage_history.append(new_coverage)

      if delta < delta_threshold:
          break   # Converged: stop reading, finalize paper

  export(draft)
```

### Anytime Algorithm Variant

Instead of a hard explore/commit boundary, maintain a "ready to export" signal that
is always true, but keep reading as long as the marginal gain justifies the cost:

```
while tokens_remaining > 0:
    p = select_next_paper()
    expected_gain = estimate_gain(p)   # e.g. dist from knowledge centroid
    cost = token_count(p) / token_budget

    if expected_gain / cost < stopping_threshold:
        break   # marginal return too low
    read(p)
```

This is an **anytime algorithm**: the agent can be interrupted at any point and will
have a valid (if incomplete) paper ready. The stopping threshold is a hyperparameter
that trades off coverage vs. speed.

### Mapping to ScholarForge

- `get_coverage_gaps()` in `agent/tools.py` already computes coverage delta and returns
  a `converged` signal — this is exactly the stopping criterion.
- The current agentic loop in `architecture.md` already implements explore-then-commit:
  read hubs → write draft → measure coverage → iterate. The optimal stopping framing
  formalizes the stopping criterion: stop when `delta < 2%` (already in the code).
- The `1/e` exploration fraction suggests: with a 50-paper corpus, read ~18 papers
  before writing the first draft. This is the warm-start that the hub-spoke strategy
  provides (read all hubs + their neighbors first).

### When does it beat Snowball?

This isn't a competing strategy — it is a **meta-controller** that decides when to
stop and layered on top of any exploration strategy. The key contribution is the
formal stopping criterion vs. Snowball's implicit stopping (token budget exhausted).

Optimal stopping reduces wasted reads: Snowball reads until the budget runs out, even
if coverage has plateaued after 30% of the budget. Stopping early frees tokens for
writing, verification, and revision.

---

## 5. Submodular Optimization: Coverage as a Set Function

### Why Coverage is Submodular

Let `f(S)` = fraction of corpus topics covered by the set S of papers read.
Submodularity means: adding a paper to a smaller set gives at least as much marginal
gain as adding it to a larger set.

**Formally**: for sets A ⊆ B and paper p not in B:
`f(A ∪ {p}) - f(A) ≥ f(B ∪ {p}) - f(B)`

This is exactly the diminishing-returns property. The coverage function over
embedding clusters is provably submodular.

**Key theorem** (Nemhauser et al., 1978): the greedy algorithm that always picks the
paper with maximum marginal gain achieves a (1 - 1/e) ≈ 63% approximation of the
optimal coverage, regardless of corpus size. No other polynomial-time algorithm
can do better (unless P=NP).

### Greedy Submodular Maximization

```
Inputs:
  papers: list
  budget: int
  clusters: dict[paper_id -> cluster_id]   # from K-means on vibes
  n_clusters: K

State:
  S = []                    # selected papers
  covered_clusters = set()  # clusters "covered" by selected papers
  cluster_coverage = {}     # fraction of each cluster covered

Algorithm:
  # Marginal gain = fraction of new cluster content added
  def marginal_gain(p, S):
      c = clusters[p]
      if c not in covered_clusters:
          return 1.0 / n_clusters   # covers a new cluster: high gain
      else:
          # Intra-cluster: gain proportional to distance from already-read papers in cluster
          cluster_already_read = [q for q in S if clusters[q] == c]
          v_p = get_vibe(p)
          if not cluster_already_read:
              return 0.5 / n_clusters
          min_dist = min(cosine_distance(v_p, get_vibe(q)) for q in cluster_already_read)
          return min_dist / n_clusters   # diminishing returns within cluster

  while tokens_remaining > 0:
      gains = {p: marginal_gain(p, S) for p in unread_papers}
      p_star = argmax(gains)

      if gains[p_star] < epsilon:
          break   # no paper adds meaningful coverage

      read(p_star)
      S.append(p_star)
      covered_clusters.add(clusters[p_star])
      tokens_remaining -= token_count(p_star)
```

### Lazy Greedy (Accelerated Variant)

The naive greedy re-computes marginal gains for all papers each round: O(N^2) total.
Lazy greedy exploits submodularity: marginal gains can only decrease, so cache them
and only recompute if the cached value is selected.

```
# Priority queue, max-heap by cached marginal gain
heap = MaxHeap([(marginal_gain(p, {}), p) for p in papers])

while tokens_remaining > 0 and heap:
    gain, p = heap.pop()
    recomputed_gain = marginal_gain(p, S)

    if recomputed_gain >= heap.peek().gain:
        # This paper is still the best — select it
        read(p); S.append(p)
        tokens_remaining -= token_count(p)
    else:
        # Gain decreased; push back with updated value
        heap.push((recomputed_gain, p))
```

Lazy greedy is typically 10-100x faster than naive greedy with identical output.

### Budget-Constrained Variant (Knapsack Submodularity)

When papers have different costs (token counts), the greedy algorithm must be modified:
also consider the **density** `marginal_gain(p) / token_count(p)`. Pick whichever gives
better coverage per token.

```
# At each step, consider two candidates:
p_density = argmax(marginal_gain(p) / token_count(p) for p in unread)
p_best    = argmax(marginal_gain(p) for p in unread)

# Select the one that gives better final coverage
# (run both greedy variants and pick the one with higher f(S) at budget exhaustion)
```

### Mapping to ScholarForge

- **Clusters**: `retrieve/strategies/topic_cluster.py` already does topic clustering.
  Use those cluster assignments as the ground set for the coverage function.
- **Marginal gain**: replace `suggest_next_papers()`'s `0.7 * orthogonality` term with
  the formal submodular marginal gain. This gives the same intuition (pick papers far
  from what you've read) but with the (1-1/e) guarantee.
- **`get_coverage_gaps()`** already computes coverage over the corpus — this is `f(S)`
  evaluated after the fact. The submodular algorithm computes it prospectively (before
  reading) to decide what to read next.
- The coverage function in `evaluate/coverage.py` (fraction of corpus chunks with a
  near neighbor in the output) is already submodular by construction.

### When does it beat Snowball?

- **Guaranteed worst-case coverage**: Snowball can achieve 0% coverage of a disconnected
  community (no citation links). The submodular greedy cannot: it sees all papers at
  every step and is forced by the marginal gain criterion to visit uncovered clusters.
- **Heterogeneous budgets**: lazy greedy with density weighting handles the case where
  some papers are long (high cost) but marginally redundant.
- **Formal stopping criterion**: when `max marginal_gain < epsilon`, you have a
  certificate that no unread paper can increase coverage by more than epsilon. Snowball
  has no equivalent.

### Implementation complexity

- Naive greedy: ~60 lines, no new dependencies.
- Lazy greedy: ~80 lines, needs a max-heap (Python `heapq`).
- Requires cluster assignments (K-means on vibes) and `get_vibe()` per paper.
- No training. No new dependencies beyond `numpy`.

---

## Strategy Comparison Summary

| Strategy | Coverage guarantee | Cost model | Needs training? | New deps? | Lines |
|---|---|---|---|---|---|
| Snowball (baseline) | None | Token budget | No | No | existing |
| UCB1 bandit | None (anytime) | Token budget | No | No | ~100 |
| Thompson sampling | None (Bayesian) | Token budget | No | No | ~80 |
| Spectral + medoid | Cluster coverage | Token budget | No | scipy, sklearn | ~80 |
| RWR (personalized PR) | None | Token budget | No | No (nx exists) | ~10 |
| Max-distance active | k-center approx | Token budget | No | No | ~40 |
| Logistic active | Max-entropy | Token budget | Yes (few-shot) | sklearn | ~70 |
| Explore-then-commit | Anytime | Token budget | No | No | meta-layer |
| Greedy submodular | (1-1/e) approx | Token budget | No | No | ~60 |
| Lazy greedy submodular | (1-1/e) approx | Token budget | No | No | ~80 |

---

## Recommended Implementation Order

**Priority 1 — High value, low effort:**

1. **Personalized PageRank (RWR)**: 10 lines. Change the `compute_metrics()` call
   in `graph/metrics.py` to pass `personalization={hub_id: 1.0}` to `nx.pagerank()`.
   Immediately improves relevance-focused exploration.

2. **Max-distance sampling**: 40 lines in `retrieve/strategies/active.py`.
   Replace the heuristic `0.7/0.3` weight in `suggest_next_papers()` with the
   k-center criterion. This is the theoretically cleaner version of what's already there.

3. **Greedy submodular**: 60 lines. Formalizes the coverage function already in
   `evaluate/coverage.py` and provides the (1-1/e) guarantee. Add as a standalone
   function `select_papers_greedy(budget, clusters, vibes)` and plug into the agentic
   loop's `suggest_next_papers()`.

**Priority 2 — Higher complexity, higher payoff:**

4. **Spectral clustering on vibes**: 80 lines. Replace or augment `topic_cluster.py`
   with K-means on paper vibes. The spectral approach gives better cluster separation
   for semantically similar but citation-disconnected papers.

5. **UCB1 bandit**: 100 lines. Add as a retrieval strategy (`retrieve/strategies/bandit.py`).
   Most useful when the agent runs multiple sessions on the same corpus (the prior
   transfers across runs).

**Priority 3 — Only if P1+P2 insufficient:**

6. **Thompson sampling**: requires tracking successes/failures across runs, needs a
   persistence layer for priors. More complex than UCB1 for modest gain.

7. **Logistic regression active learning**: useful only if the agent generates enough
   labeled data (20+ "cited/not cited" labels) per session. May not reach this
   threshold in typical runs.

---

## Key Insight: Submodular Greedy is the Theoretically Dominant Strategy

All other strategies (bandit, spectral, active learning) can be viewed as heuristics
for the same underlying problem: submodular coverage maximization under a budget
constraint. The greedy submodular algorithm solves this with a provable approximation
ratio and requires only embeddings + cluster assignments.

The (1-1/e) ≈ 63% guarantee means: if the optimal oracle (knowing all papers in
advance) achieves 90% coverage, the greedy algorithm achieves at least 57% coverage.
Snowball provides no such guarantee and can achieve 0% on disconnected communities.

**Practical recommendation**: implement greedy submodular as the default exploration
oracle, and use the existing `get_coverage_gaps()` signal for stopping. The bandit
and active-learning strategies are useful add-ons for corpora with uncertain structure,
not replacements.
