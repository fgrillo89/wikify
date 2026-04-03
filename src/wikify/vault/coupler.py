"""Bibliographic coupling between papers.

Two papers are coupled when they share references. Coupling strength is the
count of shared references (fingerprint-matched citations).
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlmodel import select

from wikify.store.db import get_session
from wikify.store.models import Citation


def _fingerprint(raw_text: str) -> str:
    """Normalise a citation string to a stable fingerprint.

    Steps:
    1. Lowercase
    2. Strip punctuation (keep alphanumeric and spaces)
    3. Collapse whitespace
    4. Take first 80 characters
    """
    text = raw_text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def compute_coupling(paper_ids: list[str], min_strength: int = 3) -> dict[str, list[str]]:
    """Compute bibliographic coupling for the given papers.

    Parameters
    ----------
    paper_ids:
        IDs of the papers to analyse.
    min_strength:
        Minimum number of shared references to count as coupled (default 3).

    Returns
    -------
    dict[str, list[str]]
        Mapping from each paper_id to a list of coupled paper_ids, sorted by
        coupling strength descending. Each paper receives at most
        the top-5 coupled partners.
    """
    if not paper_ids:
        return {}

    # ── 1. Fetch all Citations for the requested papers ───────────────────────
    with get_session() as session:
        statement = select(Citation).where(Citation.paper_id.in_(paper_ids))  # type: ignore[attr-defined]
        citations: list[Citation] = session.exec(statement).all()

    # ── 2. Build fingerprint → set of paper_ids that cite it ─────────────────
    fingerprint_to_papers: dict[str, set[str]] = defaultdict(set)
    for citation in citations:
        fp = _fingerprint(citation.raw_text)
        if fp:  # skip empty raw_text after normalisation
            fingerprint_to_papers[fp].add(citation.paper_id)

    # ── 3 & 4. Count shared references for every paper pair ──────────────────
    # pair key: (paper_a, paper_b) with paper_a < paper_b (lexicographic)
    pair_strength: dict[tuple[str, str], int] = defaultdict(int)
    for citing_papers in fingerprint_to_papers.values():
        sorted_papers = sorted(citing_papers)
        for i, pa in enumerate(sorted_papers):
            for pb in sorted_papers[i + 1 :]:
                pair_strength[(pa, pb)] += 1

    # ── 5 & 6. Build per-paper result, filter, sort, cap at 5 ────────────────
    # Accumulate (strength, other_paper) for each paper in the input set.
    paper_neighbours: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for (pa, pb), strength in pair_strength.items():
        if strength < min_strength:
            continue
        if pa in paper_ids:
            paper_neighbours[pa].append((strength, pb))
        if pb in paper_ids:
            paper_neighbours[pb].append((strength, pa))

    result: dict[str, list[str]] = {}
    for pid in paper_ids:
        neighbours = paper_neighbours.get(pid, [])
        # Sort descending by strength, then ascending by id for determinism
        neighbours.sort(key=lambda x: (-x[0], x[1]))
        result[pid] = [neighbour for _, neighbour in neighbours[:5]]

    return result
