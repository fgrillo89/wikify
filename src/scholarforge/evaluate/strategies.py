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


def greedy_submodular_order(
    max_papers: int = 20,
) -> list[tuple[str, str]]:
    """Greedy submodular: always pick the paper with highest marginal coverage gain.

    At each step, computes how much each unread paper's chunks would
    increase coverage, and picks the one with the highest delta.

    Returns list of (paper_id, "full") pairs in greedy order.
    """
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import get_chunk_embeddings
    from scholarforge.store.models import Chunk

    with get_session() as session:
        chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()

    # Get stored chunk embeddings
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    # Group chunk embeddings by paper
    paper_embs: dict[str, np.ndarray] = {}
    for c in chunks:
        emb = stored.get(c.id)
        if emb is not None:
            paper_embs.setdefault(c.paper_id, []).append(emb)
    for pid in paper_embs:
        paper_embs[pid] = np.array(paper_embs[pid])

    # All corpus embeddings (for coverage computation)
    corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
    corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
    corpus_norms[corpus_norms == 0] = 1
    corpus_embs = corpus_embs / corpus_norms

    # Greedy selection
    selected: list[str] = []
    selected_embs: list[np.ndarray] = []  # flat list of all selected papers' chunk embeddings
    remaining = set(paper_embs.keys())

    for _ in range(min(max_papers, len(remaining))):
        best_pid = None
        best_gain = -1.0

        # Current coverage baseline
        if selected_embs:
            sel_matrix = np.vstack(selected_embs)
            sel_norms = np.linalg.norm(sel_matrix, axis=1, keepdims=True)
            sel_norms[sel_norms == 0] = 1
            sel_matrix = sel_matrix / sel_norms
            baseline_sims = np.max(corpus_embs @ sel_matrix.T, axis=1)
        else:
            baseline_sims = np.zeros(len(corpus_embs))

        for pid in remaining:
            # What would coverage look like if we added this paper?
            candidate_embs = paper_embs[pid]
            cand_norms = np.linalg.norm(candidate_embs, axis=1, keepdims=True)
            cand_norms[cand_norms == 0] = 1
            candidate_normed = candidate_embs / cand_norms

            new_sims = np.max(corpus_embs @ candidate_normed.T, axis=1)
            combined_sims = np.maximum(baseline_sims, new_sims)
            # Coverage = fraction above threshold (sim > 0.5 means distance < 0.5)
            gain = float(np.mean(combined_sims > 0.5) - np.mean(baseline_sims > 0.5))

            if gain > best_gain:
                best_gain = gain
                best_pid = pid

        if best_pid is None or best_gain <= 0:
            break

        selected.append(best_pid)
        selected_embs.append(paper_embs[best_pid])
        remaining.discard(best_pid)

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
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import get_chunk_embeddings
    from scholarforge.store.models import Chunk

    with get_session() as session:
        chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()

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
