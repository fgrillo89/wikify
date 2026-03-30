"""Exploration strategy implementations for the optimization loop.

Each strategy is a function that returns a reading order: a list of
(paper_id, depth) pairs where depth is "digest", "sections", or "full".
The optimization loop uses these to simulate exploration and measure
coverage at each step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass


def _load_corpus_and_paper_embs() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Load corpus chunk embeddings and group by paper (shared by strategies).

    Returns:
        (corpus_embs, paper_embs) where corpus_embs is (N, 384) normalized
        and paper_embs maps paper_id -> (K, 384) normalized chunk embeddings.
    """
    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.embeddings import get_chunk_embeddings

    chunks = load_corpus_chunks()

    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    missing = len(all_ids) - len(stored)
    if missing > 0:
        import logging

        logging.getLogger(__name__).warning(
            "%d/%d chunks have no stored embedding — run `embed_chunks` first",
            missing,
            len(all_ids),
        )

    # Group and normalize per-paper chunk embeddings
    paper_embs_raw: dict[str, list] = {}
    for c in chunks:
        emb = stored.get(c.id)
        if emb is not None:
            paper_embs_raw.setdefault(c.paper_id, []).append(emb)

    paper_embs: dict[str, np.ndarray] = {}
    for pid, embs in paper_embs_raw.items():
        arr = np.array(embs)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        paper_embs[pid] = arr / norms

    # Full corpus matrix (normalized)
    corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
    corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
    corpus_norms[corpus_norms == 0] = 1
    corpus_embs = corpus_embs / corpus_norms

    return corpus_embs, paper_embs


def _marginal_gain(
    corpus_embs: np.ndarray,
    baseline_max_sims: np.ndarray,
    candidate_embs: np.ndarray,
    threshold: float = 0.5,
) -> float:
    """Compute marginal coverage gain from adding a candidate paper.

    Args:
        corpus_embs: (N, D) normalized corpus chunk embeddings.
        baseline_max_sims: (N,) current max similarity per corpus chunk.
        candidate_embs: (K, D) normalized chunk embeddings for the candidate.
        threshold: Similarity threshold (1 - cosine_distance_threshold).

    Returns:
        Coverage gain as a fraction of corpus chunks.
    """
    new_sims = np.max(corpus_embs @ candidate_embs.T, axis=1)
    combined = np.maximum(baseline_max_sims, new_sims)
    return float(np.mean(combined > threshold) - np.mean(baseline_max_sims > threshold))


def greedy_submodular_order(
    max_papers: int = 20,
    threshold: float = 0.5,
) -> list[tuple[str, str]]:
    """Lazy greedy submodular: pick papers by highest marginal coverage gain.

    Uses a max-heap with lazy evaluation. Since coverage is submodular,
    marginal gains only decrease as the read set grows, so stale gains
    are upper bounds. We only recompute a candidate's gain when it reaches
    the top of the heap, which typically requires O(1)-O(log N) recomputations
    per step instead of O(N).

    Complexity: O(N * K * D) for initial gains, then amortized O(K * D * log N)
    per step where N = papers, K = avg chunks/paper, D = embedding dim.
    For 500 papers this runs in seconds, not minutes.

    Args:
        max_papers: Maximum papers to select.
        threshold: Similarity threshold for coverage (default 0.5).

    Returns:
        List of (paper_id, "full") pairs in greedy order.
    """
    import heapq

    corpus_embs, paper_embs = _load_corpus_and_paper_embs()
    if not paper_embs:
        return []

    # Pre-compute per-paper similarity to corpus (reusable across gain evaluations)
    # paper_sims[pid] = (N,) max similarity from each corpus chunk to this paper's chunks
    paper_sims: dict[str, np.ndarray] = {}
    for pid, embs in paper_embs.items():
        paper_sims[pid] = np.max(corpus_embs @ embs.T, axis=1)

    # Initialize: compute all marginal gains (baseline is zero)
    baseline_max_sims = np.zeros(len(corpus_embs))
    # Max-heap (negate gains since heapq is a min-heap)
    # Entries: (-gain, iteration_computed, paper_id)
    heap: list[tuple[float, int, str]] = []
    for pid in paper_embs:
        gain = float(np.mean(paper_sims[pid] > threshold))
        heapq.heappush(heap, (-gain, 0, pid))

    selected: list[str] = []
    current_iteration = 0

    for _ in range(min(max_papers, len(paper_embs))):
        current_iteration += 1

        # Pop candidates until we find one whose gain is current
        while heap:
            neg_gain, computed_at, pid = heapq.heappop(heap)

            if pid in set(selected):
                continue  # already selected in a previous step

            if computed_at == current_iteration:
                # This gain is fresh — select this paper
                if -neg_gain <= 0:
                    return [(p, "full") for p in selected]  # no positive gain left

                selected.append(pid)
                # Update baseline: element-wise max with this paper's similarities
                baseline_max_sims = np.maximum(baseline_max_sims, paper_sims[pid])
                break

            # Stale gain — recompute with current baseline
            fresh_gain = _marginal_gain(corpus_embs, baseline_max_sims, paper_embs[pid], threshold)
            heapq.heappush(heap, (-fresh_gain, current_iteration, pid))

        else:
            break  # heap exhausted

    return [(pid, "full") for pid in selected]


def max_distance_order(
    max_papers: int = 20,
) -> list[tuple[str, str]]:
    """Max-distance (k-center): always pick the paper farthest from the read set.

    Provides a 2-approximation guarantee for coverage.
    """
    from scholarforge.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    if not vibes:
        return []

    pids = list(vibes.keys())
    vibe_matrix = np.array([vibes[pid] for pid in pids])

    # Similarity matrix
    sim_matrix = vibe_matrix @ vibe_matrix.T

    # Start with the paper most central (highest avg similarity — likely a hub)
    avg_sims = np.mean(sim_matrix, axis=1)
    first_idx = int(np.argmax(avg_sims))

    selected_indices = [first_idx]
    remaining = set(range(len(pids))) - {first_idx}

    for _ in range(min(max_papers - 1, len(remaining))):
        # For each remaining paper, compute min similarity to any selected paper
        min_sims = np.full(len(pids), 2.0)
        for idx in remaining:
            for sel_idx in selected_indices:
                min_sims[idx] = min(min_sims[idx], sim_matrix[idx, sel_idx])

        # Pick the one with the lowest min similarity (most distant)
        best_idx = None
        best_min_sim = 2.0
        for idx in remaining:
            if min_sims[idx] < best_min_sim:
                best_min_sim = min_sims[idx]
                best_idx = idx

        if best_idx is None:
            break

        selected_indices.append(best_idx)
        remaining.discard(best_idx)

    return [(pids[idx], "full") for idx in selected_indices]


def spectral_cluster_order(
    n_clusters: int = 8,
    max_papers: int = 20,
) -> list[tuple[str, str]]:
    """Spectral clustering + medoid: one representative per cluster first.

    Ensures cross-topic coverage before depth in any single topic.
    """
    from scholarforge.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    if not vibes:
        return []

    pids = list(vibes.keys())
    vibe_matrix = np.array([vibes[pid] for pid in pids])

    # K-means clustering on vibe vectors
    from sklearn.cluster import KMeans

    n_clusters = min(n_clusters, len(pids))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(vibe_matrix)

    # Find medoid of each cluster (paper closest to centroid)
    cluster_medoids: list[int] = []
    for k in range(n_clusters):
        member_indices = [i for i, label in enumerate(labels) if label == k]
        if not member_indices:
            continue
        centroid = kmeans.cluster_centers_[k]
        # Find closest member to centroid
        dists = [np.linalg.norm(vibe_matrix[i] - centroid) for i in member_indices]
        medoid_idx = member_indices[int(np.argmin(dists))]
        cluster_medoids.append(medoid_idx)

    # Round-robin: one medoid per cluster, then second-closest, etc.
    selected: list[int] = cluster_medoids[:max_papers]

    # Fill remaining with non-medoid papers by distance from selected set
    if len(selected) < max_papers:
        remaining = set(range(len(pids))) - set(selected)
        sel_vecs = vibe_matrix[selected]
        for _ in range(min(max_papers - len(selected), len(remaining))):
            best_idx = None
            best_min_sim = 2.0
            for idx in remaining:
                sims = vibe_matrix[idx] @ sel_vecs.T
                min_sim = float(np.min(sims))
                if min_sim < best_min_sim:
                    best_min_sim = min_sim
                    best_idx = idx
            if best_idx is None:
                break
            selected.append(best_idx)
            remaining.discard(best_idx)
            sel_vecs = vibe_matrix[selected]

    return [(pids[idx], "full") for idx in selected]


def hub_bfs_order(
    max_papers: int = 20,
) -> list[tuple[str, str]]:
    """Classic BFS from top-PageRank hub (baseline snowball)."""
    from collections import deque

    from scholarforge.graph.metrics import build_corpus_graph, compute_metrics

    metrics = compute_metrics()
    if not metrics.hub_papers:
        return []

    graph = build_corpus_graph()
    seed = metrics.hub_papers[0]

    visited_order: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque([seed])
    visited.add(seed)

    while queue and len(visited_order) < max_papers:
        node = queue.popleft()
        visited_order.append(node)
        neighbors = sorted(
            graph.neighbors(node),
            key=lambda n: graph[node][n].get("weight", 0),
            reverse=True,
        )
        for nb in neighbors:
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)

    return [(pid, "full") for pid in visited_order]


def compute_cumulative_coverage(
    reading_order: list[tuple[str, str]],
    threshold: float = 0.5,
) -> list[dict]:
    """Compute coverage after each paper in the reading order.

    Returns a list of dicts with coverage metrics at each step.
    """
    from scholarforge.evaluate.coverage import load_corpus_chunks
    from scholarforge.store.embeddings import get_chunk_embeddings

    chunks = load_corpus_chunks()

    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    # Build corpus embedding matrix
    corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
    corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
    corpus_norms[corpus_norms == 0] = 1
    corpus_embs = corpus_embs / corpus_norms

    # Group chunk embeddings by paper
    paper_chunk_embs: dict[str, np.ndarray] = {}
    for c in chunks:
        emb = stored.get(c.id)
        if emb is not None:
            paper_chunk_embs.setdefault(c.paper_id, []).append(emb)
    for pid in paper_chunk_embs:
        paper_chunk_embs[pid] = np.array(paper_chunk_embs[pid])

    # Simulate reading order
    results = []
    read_embs: list[np.ndarray] = []

    for i, (pid, depth) in enumerate(reading_order):
        if pid not in paper_chunk_embs:
            continue

        read_embs.append(paper_chunk_embs[pid])
        all_read = np.vstack(read_embs)
        read_norms = np.linalg.norm(all_read, axis=1, keepdims=True)
        read_norms[read_norms == 0] = 1
        all_read = all_read / read_norms

        sims = np.max(corpus_embs @ all_read.T, axis=1)
        coverage = float(np.mean(sims > (1.0 - threshold)))
        mean_sim = float(np.mean(sims))

        results.append(
            {
                "step": i + 1,
                "paper_id": pid,
                "coverage": coverage,
                "mean_similarity": mean_sim,
                "total_chunks_read": sum(len(e) for e in read_embs),
            }
        )

    return results
