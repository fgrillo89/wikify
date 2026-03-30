"""Semantic coverage metric — measures how well a review covers a corpus.

The core idea: embed both the corpus chunks and the review chunks into the
same vector space, then measure what fraction of the corpus's semantic content
has a nearby counterpart in the review.

This approximates an information-theoretic compression quality metric:
if the review is a "lossy compression" of the corpus, coverage measures
how much signal was retained vs. lost.

Metrics produced:
- coverage_ratio: fraction of corpus chunks within distance threshold of a review chunk
- mean_distance: average distance from each corpus chunk to its nearest review chunk
- uncovered_topics: corpus regions with no nearby review content (gaps)
- redundancy: review chunks that are near-duplicates of each other (waste)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass


@dataclass
class CoverageResult:
    """Semantic coverage analysis of a review against its source corpus."""

    # Core metrics
    coverage_ratio: float  # fraction of corpus chunks "covered" by review
    mean_distance: float  # avg cosine distance from corpus chunk to nearest review chunk
    median_distance: float

    # Distribution
    distances: list[float] = field(default_factory=list)  # per-corpus-chunk nearest distance
    threshold: float = 0.5  # cosine distance threshold for "covered"

    # Gaps — corpus sections not represented in the review
    uncovered_chunks: list[dict] = field(default_factory=list)  # {paper, section, distance}

    # Redundancy — review chunks that overlap too much with each other
    redundant_pairs: list[tuple[int, int, float]] = field(default_factory=list)

    # Paper-level coverage
    paper_coverage: dict[str, float] = field(default_factory=dict)  # paper_id -> coverage ratio

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Semantic Coverage: {self.coverage_ratio:.1%}",
            f"  Mean distance to nearest review chunk: {self.mean_distance:.3f}",
            f"  Median distance: {self.median_distance:.3f}",
            f"  Threshold: {self.threshold}",
            f"  Corpus chunks: {len(self.distances)}",
            f"  Uncovered chunks (>{self.threshold}): {len(self.uncovered_chunks)}",
            f"  Redundant review pairs (<0.1): {len(self.redundant_pairs)}",
        ]
        if self.paper_coverage:
            sorted_papers = sorted(self.paper_coverage.items(), key=lambda x: x[1])
            worst = sorted_papers[:3]
            lines.append("  Least covered papers:")
            for name, cov in worst:
                lines.append(f"    {name}: {cov:.1%}")
        return "\n".join(lines)


def compute_coverage(
    review_text: str,
    threshold: float = 0.5,
    chunk_size: int = 200,
) -> CoverageResult:
    """Compute semantic coverage of a review against the corpus.

    Args:
        review_text: The review markdown text.
        threshold: Cosine distance threshold. Corpus chunks closer than this
            to any review chunk are considered "covered."
        chunk_size: Approximate word count per review chunk for embedding.

    Returns:
        CoverageResult with all metrics.
    """
    import re

    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import _store
    from scholarforge.store.models import Chunk, Paper

    model = _store.model

    # 1. Get all corpus chunks and their paper info
    with get_session() as session:
        chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    if not chunks:
        return CoverageResult(
            coverage_ratio=0.0, mean_distance=1.0, median_distance=1.0, threshold=threshold
        )

    # 2. Embed corpus chunks (batch encode)
    corpus_texts = [c.content for c in chunks]
    corpus_embeddings = model.encode(corpus_texts, show_progress_bar=False, batch_size=64)
    corpus_embeddings = np.array(corpus_embeddings)

    # Normalize for cosine similarity
    corpus_norms = np.linalg.norm(corpus_embeddings, axis=1, keepdims=True)
    corpus_norms[corpus_norms == 0] = 1
    corpus_embeddings = corpus_embeddings / corpus_norms

    # 3. Chunk and embed the review
    # Strip references section
    review_body = re.split(r"\n## References\n", review_text)[0]
    # Remove headings for content-only embedding
    review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()

    # Split into chunks by word count
    words = review_body.split()
    review_chunks = []
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i : i + chunk_size])
        if len(chunk_text.strip()) > 50:
            review_chunks.append(chunk_text)

    if not review_chunks:
        return CoverageResult(
            coverage_ratio=0.0, mean_distance=1.0, median_distance=1.0, threshold=threshold
        )

    review_embeddings = model.encode(review_chunks, show_progress_bar=False, batch_size=64)
    review_embeddings = np.array(review_embeddings)
    review_norms = np.linalg.norm(review_embeddings, axis=1, keepdims=True)
    review_norms[review_norms == 0] = 1
    review_embeddings = review_embeddings / review_norms

    # 4. Compute distances: for each corpus chunk, find nearest review chunk
    # Cosine distance = 1 - cosine_similarity
    similarity_matrix = corpus_embeddings @ review_embeddings.T  # (n_corpus, n_review)
    nearest_distances = 1.0 - np.max(similarity_matrix, axis=1)  # cosine distance

    # 5. Coverage metrics
    covered = nearest_distances < threshold
    coverage_ratio = float(np.mean(covered))
    mean_dist = float(np.mean(nearest_distances))
    median_dist = float(np.median(nearest_distances))

    # 6. Identify uncovered chunks (gaps)
    uncovered = []
    for i, dist in enumerate(nearest_distances):
        if dist >= threshold:
            c = chunks[i]
            paper = papers.get(c.paper_id)
            uncovered.append(
                {
                    "paper": paper.display_name() if paper else c.paper_id[:16],
                    "section": c.section_path or "(unknown)",
                    "distance": float(dist),
                    "preview": c.content[:100],
                }
            )
    # Sort by distance descending (worst gaps first)
    uncovered.sort(key=lambda x: x["distance"], reverse=True)

    # 7. Redundancy in the review
    redundant = []
    if len(review_embeddings) > 1:
        review_sim = review_embeddings @ review_embeddings.T
        for i in range(len(review_embeddings)):
            for j in range(i + 1, len(review_embeddings)):
                dist = 1.0 - review_sim[i, j]
                if dist < 0.1:  # very similar review chunks
                    redundant.append((i, j, float(dist)))

    # 8. Per-paper coverage
    paper_chunks: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        paper = papers.get(c.paper_id)
        name = paper.display_name() if paper else c.paper_id[:16]
        paper_chunks.setdefault(name, []).append(i)

    paper_cov = {}
    for name, indices in paper_chunks.items():
        paper_dists = nearest_distances[indices]
        paper_cov[name] = float(np.mean(paper_dists < threshold))

    return CoverageResult(
        coverage_ratio=coverage_ratio,
        mean_distance=mean_dist,
        median_distance=median_dist,
        distances=[float(d) for d in nearest_distances],
        threshold=threshold,
        uncovered_chunks=uncovered[:20],  # top 20 gaps
        redundant_pairs=redundant,
        paper_coverage=paper_cov,
    )


@dataclass
class PaperVibe:
    """Synthesized embedding for a paper — its "vibe" in vector space."""

    paper_id: str
    display_name: str
    centroid: np.ndarray  # weighted average of chunk embeddings (384-dim)
    n_chunks: int
    dominant_sections: list[str]  # top section types by chunk count

    def similarity_to(self, other: PaperVibe) -> float:
        """Cosine similarity to another paper's vibe."""
        return float(
            np.dot(self.centroid, other.centroid)
            / (np.linalg.norm(self.centroid) * np.linalg.norm(other.centroid) + 1e-9)
        )


def compute_paper_vibes() -> list[PaperVibe]:
    """Compute a single "vibe" vector for each paper from its chunk embeddings.

    Uses token-weighted centroid: chunks with more content contribute more
    to the paper's overall semantic signature. This captures the paper's
    dominant themes without requiring any training.

    Returns:
        List of PaperVibe objects sorted by paper display_name.
    """
    from collections import Counter

    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.embeddings import _store
    from scholarforge.store.models import Chunk, Paper

    model = _store.model

    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}
        chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()

    if not chunks:
        return []

    # Group chunks by paper
    paper_chunks: dict[str, list[Chunk]] = {}
    for c in chunks:
        paper_chunks.setdefault(c.paper_id, []).append(c)

    # Batch-encode all chunks at once for efficiency
    all_texts = [c.content for c in chunks]
    all_embeddings = model.encode(all_texts, show_progress_bar=False, batch_size=64)

    # Map chunk id -> embedding index
    chunk_id_to_idx = {c.id: i for i, c in enumerate(chunks)}

    vibes = []
    for paper_id, p_chunks in paper_chunks.items():
        paper = papers.get(paper_id)
        if not paper:
            continue

        # Token-weighted centroid
        embeddings = []
        weights = []
        section_counter: Counter = Counter()

        for c in p_chunks:
            idx = chunk_id_to_idx[c.id]
            embeddings.append(all_embeddings[idx])
            weights.append(c.token_count)
            if c.section_type:
                section_counter[c.section_type] += 1

        emb_array = np.array(embeddings)
        weight_array = np.array(weights, dtype=float)
        weight_array /= weight_array.sum() + 1e-9

        centroid = np.average(emb_array, axis=0, weights=weight_array)
        # Normalize
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        dominant = [s for s, _ in section_counter.most_common(3)]

        vibes.append(
            PaperVibe(
                paper_id=paper_id,
                display_name=paper.display_name(),
                centroid=centroid,
                n_chunks=len(p_chunks),
                dominant_sections=dominant,
            )
        )

    vibes.sort(key=lambda v: v.display_name)
    return vibes


def vibe_map_for_llm(vibes: list[PaperVibe], top_k: int = 5) -> str:
    """Format paper vibes as a text map for LLM consumption.

    Shows each paper's nearest neighbors (by vibe similarity) so the agent
    can see which papers cover similar ground and which are unique.

    Args:
        vibes: List of PaperVibe objects.
        top_k: Number of nearest neighbors to show per paper.

    Returns:
        Markdown-formatted vibe map.
    """
    if not vibes:
        return "No paper vibes computed."

    lines = [
        "## Paper Vibe Map",
        "",
        "Each paper's semantic nearest neighbors (by content similarity).",
        "Papers with no close neighbors cover unique ground.",
        "",
    ]

    for vibe in vibes:
        # Compute similarity to all other papers
        sims = []
        for other in vibes:
            if other.paper_id != vibe.paper_id:
                sim = vibe.similarity_to(other)
                sims.append((other.display_name, sim))
        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:top_k]

        neighbors = ", ".join(f"{name} ({sim:.2f})" for name, sim in top)
        lines.append(f"- **{vibe.display_name}** [{vibe.n_chunks} chunks]: {neighbors}")

    return "\n".join(lines)
