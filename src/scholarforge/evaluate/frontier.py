"""Frontier exploration: systematically find papers at the edges of the corpus.

Instead of random walks stumbling onto frontier papers, this module
identifies them by design using embedding space density analysis.

The core idea: papers in LOW-DENSITY regions of the embedding space
are at the frontier — they cover topics that few other papers address.
These are the most valuable for gap identification and novel synthesis.

Strategies:
1. Density-ranked exploration: rank papers by local density (k-NN avg
   distance), read the lowest-density ones first
2. Anti-greedy: the opposite of greedy submodular — pick papers that
   are LEAST covered by what you've already read
3. Hybrid: greedy seeds (3 papers for baseline) then frontier exploration
"""

from __future__ import annotations

import numpy as np


def compute_paper_density() -> list[tuple[str, float, str]]:
    """Rank papers by local density in embedding space.

    Low density = frontier paper (few nearby neighbors).
    High density = mainstream paper (many similar papers).

    Returns list of (paper_id, density, display_name) sorted by density
    ascending (frontier papers first).
    """
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import get_paper_vibe_vectors
    from scholarforge.store.models import Paper

    vibes = get_paper_vibe_vectors()
    if not vibes:
        return []

    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    pids = list(vibes.keys())
    vibe_matrix = np.array([vibes[pid] for pid in pids])

    # Compute pairwise similarity
    sim_matrix = vibe_matrix @ vibe_matrix.T

    # Local density = average similarity to 5 nearest neighbors
    k = 5
    densities = []
    for i in range(len(pids)):
        sims = np.sort(sim_matrix[i])[-k - 1 : -1]  # exclude self
        density = float(np.mean(sims))
        densities.append(density)

    # Sort by density ascending (frontier first)
    ranked = sorted(zip(pids, densities), key=lambda x: x[1])
    return [
        (pid, density, papers[pid].display_name() if pid in papers else pid[:16])
        for pid, density in ranked
    ]


def frontier_exploration_order(
    max_papers: int = 20,
    n_greedy_seeds: int = 3,
) -> list[tuple[str, str, str]]:
    """Hybrid exploration: greedy seeds for baseline, then frontier papers.

    Phase 1: Pick top n_greedy_seeds by marginal coverage gain (greedy)
    Phase 2: From remaining papers, pick the ones with LOWEST density that
    are also LEAST similar to already-selected papers (anti-greedy).

    This combines the coverage guarantee of greedy with the frontier-pushing
    of density-ranked exploration.

    Returns list of (paper_id, depth, rationale) tuples.
    """
    from scholarforge.evaluate.strategies import _load_corpus_and_paper_embs, _marginal_gain

    corpus_embs, paper_embs = _load_corpus_and_paper_embs()
    if not paper_embs:
        return []

    # Phase 1: Greedy seeds
    import heapq

    paper_sims: dict[str, np.ndarray] = {}
    for pid, embs in paper_embs.items():
        paper_sims[pid] = np.max(corpus_embs @ embs.T, axis=1)

    baseline = np.zeros(len(corpus_embs))
    heap: list[tuple[float, int, str]] = []
    for pid in paper_embs:
        gain = float(np.mean(paper_sims[pid] > 0.5))
        heapq.heappush(heap, (-gain, 0, pid))

    selected: list[tuple[str, str, str]] = []
    selected_ids: set[str] = set()
    iteration = 0

    # Greedy phase
    for _ in range(min(n_greedy_seeds, len(paper_embs))):
        iteration += 1
        while heap:
            neg_gain, comp_at, pid = heapq.heappop(heap)
            if pid in selected_ids:
                continue
            if comp_at == iteration:
                selected.append((pid, "full", f"greedy seed (coverage gain: {-neg_gain:.1%})"))
                selected_ids.add(pid)
                baseline = np.maximum(baseline, paper_sims[pid])
                break
            fresh = _marginal_gain(corpus_embs, baseline, paper_embs[pid])
            heapq.heappush(heap, (-fresh, iteration, pid))

    # Phase 2: Frontier exploration (density-ranked, anti-greedy)
    density_ranked = compute_paper_density()

    # Get vibe vectors for similarity check
    from scholarforge.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()

    # Compute selected centroid
    selected_vibe_list = [vibes[pid] for pid, _, _ in selected if pid in vibes]
    if selected_vibe_list:
        selected_centroid = np.mean(selected_vibe_list, axis=0)
        selected_centroid /= np.linalg.norm(selected_centroid) + 1e-9
    else:
        selected_centroid = None

    frontier_count = 0
    for pid, density, display_name in density_ranked:
        if pid in selected_ids:
            continue
        if frontier_count >= max_papers - n_greedy_seeds:
            break

        # Anti-greedy: skip if too similar to what we already have
        if selected_centroid is not None and pid in vibes:
            sim_to_selected = float(np.dot(vibes[pid], selected_centroid))
            if sim_to_selected > 0.85:
                continue  # too similar, not frontier enough

        depth = "full" if frontier_count < 2 else "digest"
        selected.append(
            (pid, depth, f"frontier (density: {density:.3f}, rank: {frontier_count + 1})")
        )
        selected_ids.add(pid)
        frontier_count += 1

        # Update centroid with each selection
        if pid in vibes:
            all_selected_vibes = [vibes[p] for p, _, _ in selected if p in vibes]
            selected_centroid = np.mean(all_selected_vibes, axis=0)
            selected_centroid /= np.linalg.norm(selected_centroid) + 1e-9

    return selected


def format_frontier_order_for_agent(order: list[tuple[str, str, str]]) -> str:
    """Format the frontier exploration order as text for the agent."""
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    lines = ["## Frontier Exploration Order", ""]
    for i, (pid, depth, rationale) in enumerate(order, 1):
        paper = papers.get(pid)
        name = paper.display_name() if paper else pid[:20]
        lines.append(f"{i}. [{depth}] **{name}**")
        lines.append(f"   {rationale}")
    return "\n".join(lines)
