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
    from wikify.core.store.models import Chunk


def get_corpus_paper_ids() -> set[str]:
    """Return paper IDs that belong to the ingested corpus (not generated output).

    Uses the Paper.origin field: "corpus" for ingested papers, "generated" for
    writing pipeline output. This prevents generated content from contaminating
    corpus metrics like coverage, vibe vectors, and strategy ordering.
    """
    from sqlmodel import select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Paper, PaperOrigin

    with get_session() as session:
        papers = session.exec(select(Paper).where(Paper.origin == PaperOrigin.CORPUS)).all()
    return {p.id for p in papers}


def load_corpus_chunks() -> list[Chunk]:
    """Load only chunks belonging to ingested corpus papers.

    Filters out any chunks from generated output or other non-corpus sources,
    using the Paper.origin field as the authoritative source of truth.
    """
    from sqlmodel import select

    from wikify.core.store.db import get_session
    from wikify.core.store.models import Chunk

    corpus_pids = get_corpus_paper_ids()
    with get_session() as session:
        chunks = session.exec(select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)).all()
    return [c for c in chunks if c.paper_id in corpus_pids]


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

    # Paper-level coverage (keyed by display_name and by paper_id)
    paper_coverage: dict[str, float] = field(default_factory=dict)  # display_name -> ratio
    paper_id_coverage: dict[str, float] = field(default_factory=dict)  # paper_id -> ratio

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

    from wikify.core.store.db import get_session
    from wikify.core.store.embeddings import _store, get_chunk_embeddings
    from wikify.core.store.models import Paper

    # 1. Get corpus chunks only (excludes generated output)
    chunks = load_corpus_chunks()
    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    if not chunks:
        return CoverageResult(
            coverage_ratio=0.0, mean_distance=1.0, median_distance=1.0, threshold=threshold
        )

    # 2. Get corpus chunk embeddings (stored or re-encode)
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    if stored and len(stored) >= len(chunks) * 0.9:
        # Use stored embeddings
        corpus_embeddings = np.array([stored[c.id] for c in chunks if c.id in stored])
    else:
        # Fallback: encode from scratch
        model = _store.model
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

    model = _store.model
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

    # 8. Per-paper coverage (both by display_name and by paper_id)
    paper_chunks_by_name: dict[str, list[int]] = {}
    paper_chunks_by_id: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        paper = papers.get(c.paper_id)
        name = paper.display_name() if paper else c.paper_id[:16]
        paper_chunks_by_name.setdefault(name, []).append(i)
        paper_chunks_by_id.setdefault(c.paper_id, []).append(i)

    paper_cov = {}
    for name, indices in paper_chunks_by_name.items():
        paper_dists = nearest_distances[indices]
        paper_cov[name] = float(np.mean(paper_dists < threshold))

    paper_id_cov = {}
    for pid, indices in paper_chunks_by_id.items():
        paper_dists = nearest_distances[indices]
        paper_id_cov[pid] = float(np.mean(paper_dists < threshold))

    return CoverageResult(
        coverage_ratio=coverage_ratio,
        mean_distance=mean_dist,
        median_distance=median_dist,
        distances=[float(d) for d in nearest_distances],
        threshold=threshold,
        uncovered_chunks=uncovered[:20],  # top 20 gaps
        redundant_pairs=redundant,
        paper_coverage=paper_cov,
        paper_id_coverage=paper_id_cov,
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

    from wikify.core.store.db import get_session
    from wikify.core.store.embeddings import get_chunk_embeddings, get_paper_vibe_vectors
    from wikify.core.store.models import Paper

    # Load only corpus chunks (excludes generated output)
    chunks = load_corpus_chunks()
    with get_session() as session:
        papers = {p.id: p for p in session.exec(select(Paper)).all()}

    if not chunks:
        return []

    # Try stored vibe vectors first (fast path — no re-encoding)
    stored_vibes = get_paper_vibe_vectors()

    # Group chunks by paper for section info
    paper_chunks: dict[str, list[Chunk]] = {}
    for c in chunks:
        paper_chunks.setdefault(c.paper_id, []).append(c)

    # If stored vibes cover all papers, use them directly
    if stored_vibes and len(stored_vibes) >= len(paper_chunks):
        vibes = []
        for paper_id, p_chunks in paper_chunks.items():
            paper = papers.get(paper_id)
            if not paper or paper_id not in stored_vibes:
                continue
            section_counter: Counter = Counter()
            for c in p_chunks:
                if c.section_type:
                    section_counter[c.section_type] += 1
            dominant = [s for s, _ in section_counter.most_common(3)]
            vibes.append(
                PaperVibe(
                    paper_id=paper_id,
                    display_name=paper.display_name(),
                    centroid=np.array(stored_vibes[paper_id]),
                    n_chunks=len(p_chunks),
                    dominant_sections=dominant,
                )
            )
        vibes.sort(key=lambda v: v.display_name)
        return vibes

    # Fallback: compute from stored chunk embeddings
    all_ids = [c.id for c in chunks]
    stored_embs = get_chunk_embeddings(all_ids)

    # If no chunk embeddings stored, encode from scratch
    if not stored_embs:
        from wikify.core.store.embeddings import _store

        model = _store.model
        all_texts = [c.content for c in chunks]
        all_embeddings = model.encode(all_texts, show_progress_bar=False, batch_size=64)
        stored_embs = {c.id: all_embeddings[i].tolist() for i, c in enumerate(chunks)}

    vibes = []
    for paper_id, p_chunks in paper_chunks.items():
        paper = papers.get(paper_id)
        if not paper:
            continue

        # Token-weighted centroid
        embeddings = []
        weights = []
        section_counter_inner: Counter = Counter()

        for c in p_chunks:
            emb = stored_embs.get(c.id)
            if emb is not None:
                embeddings.append(emb)
                weights.append(c.token_count)
            if c.section_type:
                section_counter_inner[c.section_type] += 1

        if not embeddings:
            continue

        emb_array = np.array(embeddings)
        weight_array = np.array(weights, dtype=float)
        weight_array /= weight_array.sum() + 1e-9

        centroid = np.average(emb_array, axis=0, weights=weight_array)
        # Normalize
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        dominant = [s for s, _ in section_counter_inner.most_common(3)]

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
