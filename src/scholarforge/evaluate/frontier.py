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
    n_frontiers: int = 5,
    n_bridges: int = 3,
) -> list[tuple[str, str, str]]:
    """Hybrid exploration: greedy seeds + frontier papers + bridge papers.

    Phase 1: Pick top n_greedy_seeds by marginal coverage gain (greedy).
    Phase 2: Pick n_frontiers lowest-density papers (frontier).
    Phase 3: For each (seed, frontier) pair, find the paper closest to
             their midpoint in vibe space — the natural "stepping stone"
             that connects mainstream to edge. These are the bridge papers
             that random walks discover by accident but we find in O(N).
    Phase 4: One serendipity pick — the paper with highest dissimilarity
             to everything already selected (controlled randomness).

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

    # Get vibe vectors for all remaining phases
    from scholarforge.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    seed_ids = [pid for pid, _, _ in selected]

    # Phase 2: Frontier papers (density-ranked, anti-greedy)
    density_ranked = compute_paper_density()
    frontier_ids: list[str] = []

    frontier_count = 0
    for pid, density, display_name in density_ranked:
        if pid in selected_ids:
            continue
        if frontier_count >= n_frontiers:
            break

        # Anti-greedy: skip if too similar to seeds
        if pid in vibes:
            seed_vibes = [vibes[s] for s in seed_ids if s in vibes]
            if seed_vibes:
                max_sim = max(float(np.dot(vibes[pid], sv)) for sv in seed_vibes)
                if max_sim > 0.85:
                    continue

        depth = "full" if frontier_count < 1 else "digest"
        selected.append(
            (pid, depth, f"frontier (density: {density:.3f}, rank: {frontier_count + 1})")
        )
        selected_ids.add(pid)
        frontier_ids.append(pid)
        frontier_count += 1

    # Phase 3: Bridge papers — for each (seed, frontier) pair, find the paper
    # closest to their midpoint in vibe space. This is the "stepping stone"
    # that random walks discover by accident.
    bridge_candidates: list[tuple[str, float, str]] = []  # (pid, score, rationale)
    all_pids = list(vibes.keys())
    all_vibe_matrix = np.array([vibes[pid] for pid in all_pids])

    for seed_id in seed_ids:
        if seed_id not in vibes:
            continue
        seed_vibe = np.array(vibes[seed_id])
        for front_id in frontier_ids:
            if front_id not in vibes:
                continue
            front_vibe = np.array(vibes[front_id])

            # Midpoint between seed and frontier
            midpoint = (seed_vibe + front_vibe) / 2
            midpoint /= np.linalg.norm(midpoint) + 1e-9

            # Find the unselected paper closest to the midpoint
            sims_to_mid = all_vibe_matrix @ midpoint
            for idx in np.argsort(sims_to_mid)[::-1]:
                candidate_pid = all_pids[idx]
                if candidate_pid in selected_ids:
                    continue
                # Must be genuinely "in between" — not too close to either end
                sim_to_seed = float(np.dot(vibes[candidate_pid], seed_vibe))
                sim_to_front = float(np.dot(vibes[candidate_pid], front_vibe))
                if sim_to_seed < 0.9 and sim_to_front < 0.9:
                    mid_sim = float(sims_to_mid[idx])
                    bridge_candidates.append(
                        (
                            candidate_pid,
                            mid_sim,
                            f"bridge (mid_sim: {mid_sim:.2f})",
                        )
                    )
                    break

    # Deduplicate and pick top n_bridges
    seen_bridge_ids: set[str] = set()
    for pid, score, rationale in sorted(bridge_candidates, key=lambda x: x[1], reverse=True):
        if pid in selected_ids or pid in seen_bridge_ids:
            continue
        if len(seen_bridge_ids) >= n_bridges:
            break
        selected.append((pid, "digest", rationale))
        selected_ids.add(pid)
        seen_bridge_ids.add(pid)

    # Phase 4: Serendipity pick — most dissimilar to everything selected
    if len(selected_ids) < max_papers:
        selected_vibes = np.array([vibes[pid] for pid, _, _ in selected if pid in vibes])
        if len(selected_vibes) > 0:
            selected_centroid = np.mean(selected_vibes, axis=0)
            selected_centroid /= np.linalg.norm(selected_centroid) + 1e-9

            sims_to_centroid = all_vibe_matrix @ selected_centroid
            # Pick the LEAST similar unselected paper
            for idx in np.argsort(sims_to_centroid):
                candidate_pid = all_pids[idx]
                if candidate_pid not in selected_ids:
                    sim = float(sims_to_centroid[idx])
                    selected.append(
                        (
                            candidate_pid,
                            "digest",
                            f"serendipity (most dissimilar to read set, sim: {sim:.2f})",
                        )
                    )
                    selected_ids.add(candidate_pid)
                    break

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
