"""Comprehensive quality metrics for literature review evaluation.

Architecture: precompute-once, share everywhere.
``comprehensive_quality_report()`` builds an ``EmbeddingContext`` once
(corpus embeddings from the store + review chunk embeddings from the ONNX
model) and passes it to every metric.  No individual metric loads the corpus
or calls the model independently.

Metrics (8 total):
  1. ProseQuality            -- citation clustering, synthesis depth, voice
  2. FrontierShift           -- centroid shift toward sparse regions
  3. ArgumentativeCoherence  -- consecutive corpus-chunk-pair order preservation
  4. SemanticResidual        -- SVD projection: synthesis vs summarization
  5. BridgeVectors           -- chunks connecting DISTANT clusters
  6. GapDetection            -- void signal + gap-claim regex
  7. TopicCoverage           -- PaperTopic table coverage
  8. FactualSpecificity      -- numeric/chem/acronym density (log-scaled)

Composite weights:
  Prose quality: 0.20 | Frontier shift: 0.10 | Bridge: 0.11
  Semantic residual: 0.08 | Gap detection: 0.12 | Coherence: 0.10
  Topic coverage: 0.05 | Factual specificity: 0.12 | Semantic coverage: 0.06
  Centroid alignment: 0.06
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# EmbeddingContext: pre-computed data shared across all metrics
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingContext:
    """Pre-computed embeddings shared across all metrics.

    ``corpus_embs`` and ``corpus_chunks`` are aligned by index: row i of
    ``corpus_embs`` corresponds to ``corpus_chunks[i]``.  Both contain only
    chunks that have stored embeddings (no missing-embedding gaps).

    ``review_embs`` and ``review_chunk_texts`` are also aligned by index.
    """

    review_embs: np.ndarray  # (n_review, 384) normalized
    corpus_embs: np.ndarray  # (n_corpus, 384) normalized
    corpus_chunks: list  # Chunk objects aligned to corpus_embs
    review_chunk_texts: list[str]  # raw text for each review chunk


def _build_embedding_context(review_text: str, chunk_size: int = 150) -> Optional[EmbeddingContext]:
    """Build the shared EmbeddingContext.  Returns None if corpus is unavailable."""
    from wikify.papers.evaluate.coverage import load_corpus_chunks
    from wikify.store.embeddings import _store, get_chunk_embeddings

    chunks = load_corpus_chunks()
    if not chunks:
        return None

    # ---- corpus embeddings (fast: already stored in ChromaDB) ----------------
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    filtered_chunks = [c for c in chunks if c.id in stored]
    if not filtered_chunks:
        return None

    corpus_embs = np.array([stored[c.id] for c in filtered_chunks])
    norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    corpus_embs = corpus_embs / norms

    # ---- review embeddings (encode once) -------------------------------------
    review_body = re.split(r"\n## References\n", review_text)[0]
    review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()
    words = review_body.split()
    review_chunk_texts = [
        " ".join(words[i : i + chunk_size])
        for i in range(0, len(words), chunk_size)
        if len(" ".join(words[i : i + chunk_size]).strip()) > 50
    ]
    if not review_chunk_texts:
        return None

    model = _store.model
    rev_embs = np.array(model.encode(review_chunk_texts, show_progress_bar=False, batch_size=64))
    rev_norms = np.linalg.norm(rev_embs, axis=1, keepdims=True)
    rev_norms[rev_norms == 0] = 1
    rev_embs = rev_embs / rev_norms

    return EmbeddingContext(
        review_embs=rev_embs,
        corpus_embs=corpus_embs,
        corpus_chunks=filtered_chunks,
        review_chunk_texts=review_chunk_texts,
    )


# ---------------------------------------------------------------------------
# Metric 1: Frontier Shift
# ---------------------------------------------------------------------------


@dataclass
class FrontierShiftResult:
    """Centroid shift direction toward sparse regions of the corpus space.

    A high score means the review's semantic center of gravity pushes toward
    unexplored territory rather than restating consensus.
    """

    shift_magnitude: float  # L2 distance between corpus and review centroids
    density_at_corpus_center: float  # avg top-k similarity at the corpus centroid
    density_at_shifted_point: float  # avg top-k similarity at the extrapolated shift
    frontier_score: float  # normalized density drop in the shift direction

    def score(self) -> float:
        return max(0.0, min(1.0, self.frontier_score))

    def interpretation(self) -> str:
        lines = [
            "Frontier Shift:",
            f"  Shift magnitude: {self.shift_magnitude:.4f}",
            f"  Density at corpus center: {self.density_at_corpus_center:.3f}",
            f"  Density at shifted point: {self.density_at_shifted_point:.3f}",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.3:
            lines.append(
                "  Interpretation: Review pushes toward frontier -- shift aims at sparse regions."
            )
        elif s > 0.1:
            lines.append(
                "  Interpretation: Moderate frontier push -- some movement toward new territory."
            )
        else:
            lines.append(
                "  Interpretation: Review stays near consensus -- shift aims at dense regions."
            )
        return "\n".join(lines)


def compute_frontier_shift(ctx: EmbeddingContext) -> FrontierShiftResult:
    """Compute frontier shift from pre-computed embeddings."""
    # Token-weighted corpus centroid
    weights = np.array([c.token_count for c in ctx.corpus_chunks], dtype=float)
    weights /= weights.sum() + 1e-9
    corpus_centroid = np.average(ctx.corpus_embs, axis=0, weights=weights)
    corpus_centroid /= np.linalg.norm(corpus_centroid) + 1e-9

    # Review centroid (unweighted)
    review_centroid = np.mean(ctx.review_embs, axis=0)
    review_centroid /= np.linalg.norm(review_centroid) + 1e-9

    shift = review_centroid - corpus_centroid
    shift_magnitude = float(np.linalg.norm(shift))

    if shift_magnitude < 1e-8:
        return FrontierShiftResult(
            shift_magnitude=0.0,
            density_at_corpus_center=0.0,
            density_at_shifted_point=0.0,
            frontier_score=0.0,
        )

    k = min(20, len(ctx.corpus_embs))

    def density_at(point: np.ndarray) -> float:
        p = point / (np.linalg.norm(point) + 1e-9)
        sims = ctx.corpus_embs @ p
        return float(np.mean(np.sort(sims)[-k:]))

    corpus_density = density_at(corpus_centroid)
    shifted_point = corpus_centroid + shift * 2  # extrapolate beyond review centroid
    shifted_density = density_at(shifted_point)

    density_drop = corpus_density - shifted_density
    frontier_score = max(0.0, density_drop / 0.15)  # 0.15 drop = score 1.0

    return FrontierShiftResult(
        shift_magnitude=shift_magnitude,
        density_at_corpus_center=corpus_density,
        density_at_shifted_point=shifted_density,
        frontier_score=min(1.0, frontier_score),
    )


# ---------------------------------------------------------------------------
# Metric 2: Argumentative Coherence
# ---------------------------------------------------------------------------


@dataclass
class ArgumentativeCoherenceResult:
    """Measures preservation of argumentative chains from the corpus.

    For each paper, consecutive chunk pairs form a logical chain.  If both
    chunks are matched by the review, this checks whether the matching review
    positions are nearby (preserving sequential flow).
    """

    total_chains: int
    chains_both_covered: int
    chains_order_preserved: int
    chain_preservation_ratio: float

    def score(self) -> float:
        if self.chains_both_covered == 0:
            return 0.5  # no evidence either way
        return max(0.0, min(1.0, self.chain_preservation_ratio))

    def interpretation(self) -> str:
        lines = [
            "Argumentative Coherence:",
            f"  Corpus argument chains: {self.total_chains}",
            f"  Chains covered by review: {self.chains_both_covered}",
            f"  Chains with preserved order: {self.chains_order_preserved}",
            f"  Preservation ratio: {self.chain_preservation_ratio:.3f}",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.7:
            lines.append("  Interpretation: Strong coherence -- review preserves logical flow.")
        elif s > 0.4:
            lines.append("  Interpretation: Moderate coherence -- some chains preserved.")
        else:
            lines.append("  Interpretation: Weak coherence -- related ideas are scattered.")
        return "\n".join(lines)


def compute_argumentative_coherence(
    ctx: EmbeddingContext,
    proximity_window: int = 3,
    similarity_threshold: float = 0.5,
) -> ArgumentativeCoherenceResult:
    """Measure chain preservation using pre-computed embeddings."""
    # Group corpus chunks (and their row indices) by paper, preserving order
    paper_indices: dict[str, list[int]] = {}
    for idx, chunk in enumerate(ctx.corpus_chunks):
        paper_indices.setdefault(chunk.paper_id, []).append(idx)

    total_chains = 0
    chains_both_covered = 0
    chains_order_preserved = 0

    for paper_id, indices in paper_indices.items():
        if len(indices) < 2:
            continue

        p_embs = ctx.corpus_embs[indices]  # (n_paper_chunks, 384)
        # Similarity matrix: (n_paper_chunks, n_review_chunks)
        sim_matrix = p_embs @ ctx.review_embs.T

        best_review_pos = np.argmax(sim_matrix, axis=1)
        best_review_sim = np.max(sim_matrix, axis=1)

        for i in range(len(indices) - 1):
            total_chains += 1
            if (
                best_review_sim[i] >= similarity_threshold
                and best_review_sim[i + 1] >= similarity_threshold
            ):
                chains_both_covered += 1
                gap = abs(int(best_review_pos[i + 1]) - int(best_review_pos[i]))
                if gap <= proximity_window:
                    chains_order_preserved += 1

    ratio = chains_order_preserved / max(chains_both_covered, 1) if chains_both_covered > 0 else 0.0

    return ArgumentativeCoherenceResult(
        total_chains=total_chains,
        chains_both_covered=chains_both_covered,
        chains_order_preserved=chains_order_preserved,
        chain_preservation_ratio=ratio,
    )


# ---------------------------------------------------------------------------
# Metric 3: Semantic Residual
# ---------------------------------------------------------------------------


@dataclass
class SemanticResidualResult:
    """Synthesis vs summarization via SVD subspace projection.

    Each review chunk is projected onto the principal subspace of the corpus.
    The residual (what's left) represents genuinely novel content.
    """

    avg_residual_norm: float
    avg_projection_sim: float
    avg_relevance: float
    synthesis_chunks: int  # high residual AND high relevance
    summarization_chunks: int  # low residual
    hallucination_chunks: int  # high residual AND low relevance
    total_chunks: int

    def score(self) -> float:
        if self.total_chunks == 0:
            return 0.0
        synth_ratio = self.synthesis_chunks / self.total_chunks
        hallu_penalty = self.hallucination_chunks / self.total_chunks
        return max(0.0, min(1.0, synth_ratio - hallu_penalty * 0.5))

    def interpretation(self) -> str:
        lines = [
            "Semantic Residual:",
            f"  Avg residual norm: {self.avg_residual_norm:.3f}",
            f"  Avg projection similarity: {self.avg_projection_sim:.3f}",
            f"  Avg corpus relevance: {self.avg_relevance:.3f}",
            f"  Synthesis / summarization / hallucination chunks: "
            f"{self.synthesis_chunks} / {self.summarization_chunks} / {self.hallucination_chunks}"
            f" of {self.total_chunks}",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.3:
            lines.append(
                "  Interpretation: Strong value-add -- review synthesizes beyond the corpus."
            )
        elif s > 0.1:
            lines.append(
                "  Interpretation: Moderate value-add -- mix of summarization and synthesis."
            )
        else:
            lines.append(
                "  Interpretation: Primarily summarization -- review mostly restates corpus."
            )
        return "\n".join(lines)


def compute_semantic_residual(
    ctx: EmbeddingContext,
    n_basis: int = 50,
) -> SemanticResidualResult:
    """Project review chunks onto corpus SVD subspace and measure residuals."""
    from sklearn.decomposition import TruncatedSVD

    n_basis = min(n_basis, len(ctx.corpus_embs) - 1, ctx.corpus_embs.shape[1] - 1)
    svd = TruncatedSVD(n_components=n_basis, random_state=42)
    svd.fit(ctx.corpus_embs)
    basis = svd.components_  # (n_basis, 384)

    # Project review chunks
    coefficients = ctx.review_embs @ basis.T  # (n_review, n_basis)
    projections = coefficients @ basis  # (n_review, 384)
    residuals = ctx.review_embs - projections
    residual_norms = np.linalg.norm(residuals, axis=1)

    proj_norms = np.linalg.norm(projections, axis=1, keepdims=True)
    proj_norms[proj_norms == 0] = 1
    projection_sims = np.sum(ctx.review_embs * (projections / proj_norms), axis=1)

    # Relevance: similarity to nearest corpus chunk
    sim_to_corpus = ctx.review_embs @ ctx.corpus_embs.T
    relevance = np.max(sim_to_corpus, axis=1)

    residual_threshold = float(np.median(residual_norms))

    synthesis = summarization = hallucination = 0
    for i in range(len(ctx.review_embs)):
        high_residual = bool(residual_norms[i] > residual_threshold)
        high_relevance = bool(relevance[i] > 0.5)

        if high_residual and high_relevance:
            synthesis += 1
        elif not high_residual:
            summarization += 1
        else:
            hallucination += 1

    return SemanticResidualResult(
        avg_residual_norm=float(np.mean(residual_norms)),
        avg_projection_sim=float(np.mean(projection_sims)),
        avg_relevance=float(np.mean(relevance)),
        synthesis_chunks=synthesis,
        summarization_chunks=summarization,
        hallucination_chunks=hallucination,
        total_chunks=len(ctx.review_embs),
    )


# ---------------------------------------------------------------------------
# Metric 4: Bridge Vectors (FIXED)
# ---------------------------------------------------------------------------


@dataclass
class BridgeVectorResult:
    """Review chunks that connect DISTANT corpus clusters.

    A bridge chunk must be similar to 2+ clusters that are themselves far
    apart (inter-cluster distance > median inter-cluster distance).  This
    prevents a homogeneous corpus from saturating the metric.
    """

    total_review_chunks: int
    bridge_chunks: int
    bridge_ratio: float
    median_inter_cluster_distance: float  # reference distance threshold
    avg_clusters_bridged: float

    def score(self) -> float:
        # 33% bridge chunks = score 1.0
        return min(1.0, self.bridge_ratio * 3)

    def interpretation(self) -> str:
        lines = [
            "Bridge Vectors:",
            f"  Review chunks: {self.total_review_chunks}",
            f"  Bridge chunks (connect 2+ distant clusters): {self.bridge_chunks}"
            f" ({self.bridge_ratio:.1%})",
            f"  Median inter-cluster distance: {self.median_inter_cluster_distance:.3f}",
            f"  Avg clusters bridged per bridge chunk: {self.avg_clusters_bridged:.1f}",
            f"  Score: {self.score():.3f}",
        ]
        if self.bridge_ratio > 0.2:
            lines.append(
                "  Interpretation: Strong cross-cluster synthesis -- connects distant themes."
            )
        elif self.bridge_ratio > 0.05:
            lines.append(
                "  Interpretation: Some bridging -- review makes connections between a few themes."
            )
        else:
            lines.append(
                "  Interpretation: Weak bridging -- review stays within established clusters."
            )
        return "\n".join(lines)


def compute_bridge_vectors(
    ctx: EmbeddingContext,
    paper_dissim_threshold: float = 0.80,
    min_review_sim: float = 0.45,
    min_secondary_sim: float = 0.35,
) -> BridgeVectorResult:
    """Find review chunks that bridge dissimilar papers.

    Paper-level approach: for each review chunk, find the two nearest
    papers (by vibe vector). If both are reasonably close AND the two
    papers are themselves dissimilar (similarity < threshold), the chunk
    is synthesizing across different works.

    This avoids the cluster-centroid problem where no chunk can be
    geometrically close to two distant centroids.
    """
    from wikify.store.embeddings import get_paper_vibe_vectors

    vibes = get_paper_vibe_vectors()
    if not vibes:
        return BridgeVectorResult(
            total_review_chunks=len(ctx.review_embs),
            bridge_chunks=0,
            bridge_ratio=0.0,
            median_inter_cluster_distance=0.0,
            avg_clusters_bridged=0.0,
        )

    pids = list(vibes.keys())
    vibe_matrix = np.array([vibes[pid] for pid in pids])
    paper_sims = vibe_matrix @ vibe_matrix.T

    # Review chunk similarity to each paper vibe
    rev_to_papers = ctx.review_embs @ vibe_matrix.T  # (n_review, n_papers)

    bridge_chunks = 0
    dissimilarities: list[float] = []

    for i in range(len(ctx.review_embs)):
        top2_indices = np.argsort(rev_to_papers[i])[-2:][::-1]
        top2_sims = rev_to_papers[i][top2_indices]

        # Both papers must be reasonably similar to the review chunk
        if top2_sims[0] < min_review_sim or top2_sims[1] < min_secondary_sim:
            continue

        # The two papers must be dissimilar to each other
        inter_paper_sim = float(paper_sims[top2_indices[0], top2_indices[1]])
        if inter_paper_sim < paper_dissim_threshold:
            bridge_chunks += 1
            dissimilarities.append(1.0 - inter_paper_sim)

    total = len(ctx.review_embs)
    # Compute median inter-paper distance for context
    all_paper_sims = []
    for i in range(len(pids)):
        for j in range(i + 1, len(pids)):
            all_paper_sims.append(paper_sims[i, j])
    median_dist = float(1.0 - np.median(all_paper_sims)) if all_paper_sims else 0.0

    return BridgeVectorResult(
        total_review_chunks=total,
        bridge_chunks=bridge_chunks,
        bridge_ratio=bridge_chunks / max(total, 1),
        median_inter_cluster_distance=median_dist,
        avg_clusters_bridged=float(np.mean(dissimilarities)) if dissimilarities else 0.0,
    )


# ---------------------------------------------------------------------------
# Metric 5: Gap Detection (FIXED — two-signal approach)
# ---------------------------------------------------------------------------

_GAP_PHRASES = re.compile(
    r"remains?\s+unexplored|no\s+(?:published\s+)?stud(?:y|ies)\s+ha(?:s|ve)|"
    r"future\s+work\s+should|not\s+yet\s+(?:been\s+)?investigated|"
    r"gap\s+(?:between|in)|little\s+is\s+known|no\s+systematic\s+study|"
    r"has\s+not\s+been\s+(?:examined|reported|demonstrated)|open\s+question|"
    r"remains?\s+(?:unclear|unknown)|"
    r"remains?\s+to\s+be\s+(?:studied|investigated|explored)|"
    r"lack(?:s|ing)?\s+of\s+(?:studies|research|data|evidence)|"
    r"insufficient(?:ly)?\s+(?:studied|explored|investigated)|"
    r"\bunexplored\b|\bunderexplored\b|\boverlooked\b|\bunanswered\b|"
    r"\bunderst(?:udied|ood)\b|yet\s+to\s+be\s+\w+ed|"
    r"no\s+report\b|not\s+been\s+reported|warrant(?:s)?\s+further|"
    r"deserves?\s+(?:further\s+)?(?:attention|investigation|study)|"
    r"\babsent\b.*(?:data|studies|evidence|report)|"
    r"represent(?:s|ing)?\s+an?\s+(?:open|unexplored|untapped)",
    re.IGNORECASE,
)


@dataclass
class GapDetectionResult:
    """Two-signal gap detection: embedding voids + explicit gap claims.

    void_ratio: fraction of review chunks in sparse corpus regions.
    gap_claim_ratio: fraction of review sentences containing gap-indicating language.
    Score combines void exploration and gap claim density.
    A review with 5+ gap sentences in 200 total sentences (~2.5%) scores well.
    """

    void_chunks: int
    total_review_chunks: int
    void_ratio: float
    gap_sentences: int
    total_sentences: int
    gap_claim_ratio: float

    def score(self) -> float:
        # Scale gap_claim_ratio: 5% gap sentences = score 1.0
        # (9 gaps in 200 sentences = 4.5% -> score ~0.9)
        import math

        claim_score = min(1.0, math.log1p(self.gap_claim_ratio * 100) / math.log1p(5))
        return max(0.0, 0.3 * min(self.void_ratio * 5, 1.0) + 0.7 * claim_score)

    def interpretation(self) -> str:
        lines = [
            "Gap Detection:",
            f"  Void chunks: {self.void_chunks}/{self.total_review_chunks} ({self.void_ratio:.1%})",
            f"  Gap-claim sentences: {self.gap_sentences}/{self.total_sentences}"
            f" ({self.gap_claim_ratio:.1%})",
            f"  Score (0.3*void + 0.7*claims): {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.3:
            lines.append("  Interpretation: Review actively identifies research gaps.")
        elif s > 0.1:
            lines.append("  Interpretation: Review mentions some gaps but coverage is limited.")
        else:
            lines.append("  Interpretation: Review does not meaningfully identify gaps.")
        return "\n".join(lines)


def compute_gap_detection(ctx: EmbeddingContext, review_text: str) -> GapDetectionResult:
    """Two-signal gap detection using pre-computed embeddings."""
    # --- Signal A: embedding voids (relaxed threshold 0.45) ---
    k = min(10, len(ctx.corpus_embs))
    sim_matrix = ctx.review_embs @ ctx.corpus_embs.T  # (n_review, n_corpus)
    top_k_sims = np.sort(sim_matrix, axis=1)[:, -k:]
    avg_top_k = np.mean(top_k_sims, axis=1)

    void_threshold = 0.45  # relaxed from original 0.50
    void_chunks = int(np.sum(avg_top_k < void_threshold))
    void_ratio = void_chunks / max(len(ctx.review_embs), 1)

    # --- Signal B: gap-claim regex on raw review text ---
    review_body = re.split(r"\n## References\n", review_text)[0]
    review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()
    sentences = re.split(r"(?<=[.!?])\s+", review_body)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    total_sentences = max(len(sentences), 1)
    gap_sentences = sum(1 for s in sentences if _GAP_PHRASES.search(s))
    gap_claim_ratio = gap_sentences / total_sentences

    return GapDetectionResult(
        void_chunks=void_chunks,
        total_review_chunks=len(ctx.review_embs),
        void_ratio=void_ratio,
        gap_sentences=gap_sentences,
        total_sentences=total_sentences,
        gap_claim_ratio=gap_claim_ratio,
    )


# ---------------------------------------------------------------------------
# Metric 6: Topic Coverage
# ---------------------------------------------------------------------------


@dataclass
class TopicCoverageResult:
    """Topic-level coverage: which PaperTopic entries appear in the review."""

    topics_in_corpus: int
    topics_covered: int
    topics_omitted: list[str] = field(default_factory=list)
    coverage_ratio: float = 0.0
    topic_detail: dict[str, str] = field(default_factory=dict)

    def score(self) -> float:
        return max(0.0, min(1.0, self.coverage_ratio))

    def interpretation(self) -> str:
        lines = [
            "Topic Coverage:",
            f"  Corpus topics: {self.topics_in_corpus}",
            f"  Topics covered: {self.topics_covered} ({self.coverage_ratio:.1%})",
            f"  Score: {self.score():.3f}",
        ]
        if self.topics_omitted:
            lines.append(f"  Omitted topics (sample): {', '.join(self.topics_omitted[:10])}")
        return "\n".join(lines)


def compute_topic_coverage(review_text: str) -> Optional[TopicCoverageResult]:
    """Check which PaperTopic entries appear in the review (no embeddings needed)."""
    try:
        from sqlmodel import select

        from wikify.store.db import get_session
        from wikify.store.models import PaperTopic

        with get_session() as session:
            all_topics = session.exec(select(PaperTopic)).all()

        # English filler words and metadata noise that slip into paper keyword fields
        _topic_stop_words: frozenset[str] = frozenset(
            {
                "abstract",
                "additionally",
                "also",
                "although",
                "august 2020",
                "based on this extensive study",
                "by applying different conditional stimuli",
                "closely resembling long-term potentiation",
                "exhibiting reliable bipolar resistive switching",
                "for this purpose",
                "furthermore",
                "here",
                "however",
                "importantly",
                "in addition",
                "in this study",
                "in this work",
                "including forming-free",
                "including potentiation",
                "including spike-amplitude-",
                "including ultra-fast switching",
                "initially",
                "inspired by biological synapse",
                "it faces challenges with uniformity",
                "journal citation and doi",
                "journalcitation and doi",
                "notably",
                "offering energy effciency",
                "original content from resistive switching",
                "outstanding 10 7 pulse endurance",
                "respectively",
                "showing its excellent per-formance characteristics",
                "sndp)",
                "srdp",
                "such as long-term potentiation (ltp)",
                "such as potentiation/depression",
                "swdp",
                "therefore",
                "through careful analysi",
                "through conductance modulation",
                "thus",
                "to address these issue",
                "training effect",
                "vol",
            }
        )

        # Normalize topics: merge plurals before dedup
        raw_topics: set[str] = set()
        for t in all_topics:
            if not (3 <= len(t.topic) <= 60):
                continue
            if "<" in t.topic or "|" in t.topic or "." in t.topic[:3]:
                continue
            key = t.topic.strip().lower()
            # Skip filler words and metadata noise
            if key in _topic_stop_words:
                continue
            # Skip conjunctive fragments ("And X", "Or X") — partial keyword extractions
            if key.startswith(("and ", "or ", "but ", "with ", "for ")):
                continue
            if key.endswith("ies") and len(key) > 5:
                key = key[:-3] + "y"
            elif (
                key.endswith("s")
                and not key.endswith("ss")
                and not key.endswith("us")
                and len(key) > 4
            ):
                key = key[:-1]
            raw_topics.add(key.title() if len(key) > 4 else key.upper())
        topic_names = sorted(raw_topics)
        if not topic_names:
            return None

        review_lower = review_text.lower()
        detail: dict[str, str] = {}
        covered = 0
        omitted: list[str] = []

        for topic in topic_names:
            topic_lower = topic.lower()
            if topic_lower in review_lower:
                detail[topic] = "covered"
                covered += 1
            else:
                detail[topic] = "omitted"
                omitted.append(topic)

        return TopicCoverageResult(
            topics_in_corpus=len(topic_names),
            topics_covered=covered,
            topics_omitted=omitted,
            coverage_ratio=covered / max(len(topic_names), 1),
            topic_detail=detail,
        )
    except Exception as exc:
        raise RuntimeError(f"Topic coverage computation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Metric 7: Factual Specificity
# ---------------------------------------------------------------------------

_UNIT_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?"
    r"\s*"
    r"(?:nm|[AÅ]|[Mm]?[Vv]|[Mm]?[Aa]|[KkMmGgTt]?Hz|cycles?|%|K|"
    r"°?C|eV|cm[-−]?\d*|[Mm]?W|[Mm][Tt]orr|Pa|[mμ]?s|sccm|"
    r"[Aa]tm|J|kJ|nF|pF|fF|[Mm][Oo]hm|Ohm|ohm|[Kk][Oo]hm|"
    r"GHz|MHz|kHz|Hz|cycles|ps|fs|ns|\bT\b|\bG\b)"
    r"\b",
    re.IGNORECASE,
)
_CHEM_FORMULA = re.compile(r"\b(?:[A-Z][a-z]?\d*[0-9₀₁₂₃₄₅₆₇₈₉]*){2,}\b")
_AUTHOR_ET_AL = re.compile(r"\b[A-Z][a-z]+ et al\.", re.IGNORECASE)
_COMPARATIVE = re.compile(
    r"[^.!?]*\b\d+(?:\.\d+)?\s*[a-zA-Z%°]*[^.!?]*\b\d+(?:\.\d+)?\s*[a-zA-Z%°]*[^.!?]*[.!?]"
)
_ACRONYMS = re.compile(
    r"\b(?:ALD|CVD|PVD|MOCVD|MBE|CMOS|DRAM|SRAM|ReRAM|PCM|MRAM|STT|RRAM|"
    r"MOSFET|FinFET|GAA|NAND|NOR|XOR|LSTM|CNN|RNN|STDP|LTP|LTD|STP|"
    r"HRS|LRS|BEOL|FEOL|CMP|RIE|ALE|TEM|XPS|HRTEM|AFM|XRD|SIMS|EDS)\b"
)


@dataclass
class FactualSpecificityResult:
    """Counts of factual markers in the review."""

    numeric_with_units: int
    chemical_formulas: int
    author_citations: int
    comparative_sentences: int
    technical_acronyms: int
    word_count: int

    def score(self) -> float:
        """Log-scaled specificity score per 1,000 words.

        Thresholds: 10 markers/1kw -> 0.53, 20 -> 0.69, 40 -> 0.84, 80 -> 1.0
        """
        if self.word_count == 0:
            return 0.0
        raw = (
            self.numeric_with_units * 2.0
            + self.chemical_formulas * 1.5
            + self.author_citations * 1.0
            + self.comparative_sentences * 1.5
            + self.technical_acronyms * 0.5
        )
        per_1k = raw / max(self.word_count, 1) * 1000
        return min(math.log1p(per_1k) / math.log1p(80), 1.0)

    def interpretation(self) -> str:
        wc = max(self.word_count, 1)
        lines = [
            "Factual Specificity:",
            f"  Word count: {self.word_count:,}",
            f"  Numeric values with units: {self.numeric_with_units}"
            f" ({self.numeric_with_units / wc * 1000:.1f}/1k words)",
            f"  Chemical formulas: {self.chemical_formulas}"
            f" ({self.chemical_formulas / wc * 1000:.1f}/1k words)",
            f"  Author citations (et al.): {self.author_citations}",
            f"  Comparative sentences: {self.comparative_sentences}",
            f"  Technical acronyms: {self.technical_acronyms}",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s < 0.2:
            lines.append("  Interpretation: Low specificity -- review is vague or general.")
        elif s < 0.5:
            lines.append("  Interpretation: Moderate specificity -- some quantitative depth.")
        else:
            lines.append("  Interpretation: High specificity -- review is data-rich.")
        return "\n".join(lines)


def compute_factual_specificity(review_text: str) -> FactualSpecificityResult:
    """Count factual markers: numbers+units, formulas, citations, comparisons."""
    body = re.sub(r"^#+\s.*$", "", review_text, flags=re.MULTILINE)
    body = re.sub(r"\[.*?\]\(.*?\)", "", body)

    word_count = len(body.split())

    chem_matches = {m for m in _CHEM_FORMULA.findall(body) if len(m) >= 3}

    return FactualSpecificityResult(
        numeric_with_units=len(_UNIT_PATTERN.findall(body)),
        chemical_formulas=len(chem_matches),
        author_citations=len(_AUTHOR_ET_AL.findall(body)),
        comparative_sentences=len(_COMPARATIVE.findall(body)),
        technical_acronyms=len(set(_ACRONYMS.findall(body))),
        word_count=word_count,
    )


# ---------------------------------------------------------------------------
# Prose Quality Metrics (5 new metrics from PI review)
# ---------------------------------------------------------------------------


@dataclass
class ProseQualityResult:
    """Five metrics that capture writing quality beyond content presence.

    These detect the difference between a soulless paper catalog and
    genuine analytical prose — what the previous 9 metrics missed.
    """

    # 1. Citation Clustering: multi-cite sentences vs one-cite-per-sentence
    citation_clustering_ratio: float  # unique_refs / cited_sentences (>1.5 = synthesis)
    multi_cite_fraction: float  # fraction of cited sentences with 2+ refs

    # 2. Synthesis Depth: cross-paper reasoning with causal connectors
    deep_synthesis_fraction: float  # 2+ citations + causal connector
    surface_comparison_fraction: float  # 2+ citations, no causal reasoning
    single_paper_fraction: float  # 1 citation, no comparison (paper listing signal)

    # 3. Sentence-Opening Entropy + author-et-al detection
    opening_entropy: float  # normalized Shannon entropy (>0.7 = varied)
    author_et_al_fraction: float  # fraction starting with "[Name] et al."

    def score(self) -> float:
        """Composite prose quality score [0, 1].

        Designed so that S5_gap_structured (PI: 6.8) scores highest and
        S5_injected (PI: 4.3) scores lowest.
        """
        # Citation clustering: 0 at ratio 1.0, 1 at ratio 2.0+
        cite_score = min(1.0, max(0.0, (self.citation_clustering_ratio - 1.0)))

        # Synthesis: reward low single-paper fraction, reward surface comparison
        # Single-paper: 0 at 50% (all listing), 1 at 20% (mostly synthesis)
        listing_penalty = max(0.0, min(1.0, (0.50 - self.single_paper_fraction) / 0.30))

        # Surface + deep synthesis: 0 at 0%, 1 at 10%+
        synth_score = min(
            1.0,
            (self.surface_comparison_fraction + self.deep_synthesis_fraction * 3) / 0.10,
        )

        # Opening entropy with author-et-al penalty
        entropy_score = self.opening_entropy
        author_penalty = min(0.5, self.author_et_al_fraction * 2)

        return (
            0.25 * cite_score
            + 0.30 * listing_penalty
            + 0.20 * synth_score
            + 0.25 * max(0, entropy_score - author_penalty)
        )

    def interpretation(self) -> str:
        lines = [
            "Prose Quality:",
            f"  Citation clustering: {self.citation_clustering_ratio:.2f}"
            f" (multi-cite: {self.multi_cite_fraction:.0%})",
            f"  Synthesis: deep={self.deep_synthesis_fraction:.1%}"
            f" surface={self.surface_comparison_fraction:.1%}"
            f" single-paper={self.single_paper_fraction:.0%}",
            f"  Sentence-opening entropy: {self.opening_entropy:.3f}"
            f" (author-et-al: {self.author_et_al_fraction:.0%})",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.5:
            lines.append("  Interpretation: Analytical prose with synthesis and voice.")
        elif s > 0.25:
            lines.append("  Interpretation: Mixed — some synthesis but paper-listing tendencies.")
        else:
            lines.append("  Interpretation: Paper-listing mode with repetitive patterns.")
        return "\n".join(lines)


def compute_prose_quality(review_text: str) -> ProseQualityResult:
    """Compute 5 prose quality metrics from review text (no embeddings needed)."""
    body = re.split(r"\n## References\n", review_text)[0]
    body = re.sub(r"^#+\s.*$", "", body, flags=re.MULTILINE)  # strip headings

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 15]
    total_sentences = max(len(sentences), 1)

    # Citation pattern: numbered refs [1] or author-year refs [REF:Smith 2020 - Title]
    _cite_pat = re.compile(r"\[\d+\]|\[REF:[^\]]+\]")

    # --- 1. Citation Clustering ---
    cited_sentences = [s for s in sentences if _cite_pat.search(s)]
    total_cited = max(len(cited_sentences), 1)

    multi_cite = sum(1 for s in cited_sentences if len(_cite_pat.findall(s)) >= 2)
    total_refs_in_cited = sum(len(_cite_pat.findall(s)) for s in cited_sentences)

    clustering_ratio = total_refs_in_cited / total_cited
    multi_cite_frac = multi_cite / total_cited

    # --- 2. Synthesis Depth ---
    _causal = re.compile(
        r"\b(?:suggest(?:s|ing)|implicat(?:e|es|ing)|indicat(?:e|es|ing)|"
        r"confirm(?:s|ing)|reveal(?:s|ing)|demonstrat(?:e|es|ing)\s+that|"
        r"attribut(?:e|ed)\s+to|consistent\s+with|"
        r"because|therefore|consequently|thus|hence|"
        r"this\s+(?:difference|discrepancy|divergence|trend)|"
        r"the\s+(?:mechanism|origin|cause|reason)|"
        r"depend(?:s|ing)\s+on|correlat(?:e|es|ion))\b",
        re.IGNORECASE,
    )
    # Multi-cite: two or more citation markers (any style) in one sentence
    _multi_cite = re.compile(r"(?:\[\d+\]|\[REF:[^\]]+\]).*?(?:\[\d+\]|\[REF:[^\]]+\])")

    deep_synthesis = sum(1 for s in sentences if _multi_cite.search(s) and _causal.search(s))
    surface_comparison = sum(
        1 for s in sentences if _multi_cite.search(s) and not _causal.search(s)
    )
    single_paper = sum(1 for s in sentences if len(_cite_pat.findall(s)) == 1)

    deep_synth_frac = deep_synthesis / total_sentences
    surface_comp_frac = surface_comparison / total_sentences
    single_paper_frac = single_paper / total_sentences

    # --- 3. Sentence-Opening Entropy ---
    openings = []
    author_et_al = 0
    _author_pattern = re.compile(
        r"^[A-Z][a-z]+\s+(?:and\s+colleagues|et\s+al\.?|and\s+co-?workers)",
        re.IGNORECASE,
    )
    for s in sentences:
        tokens = s.split()[:3]
        if tokens:
            openings.append(" ".join(tokens).lower())
        if _author_pattern.match(s):
            author_et_al += 1

    # Shannon entropy
    from collections import Counter

    opening_counts = Counter(openings)
    total_openings = max(sum(opening_counts.values()), 1)
    entropy = 0.0
    for count in opening_counts.values():
        p = count / total_openings
        if p > 0:
            entropy -= p * math.log2(p)
    max_entropy = math.log2(max(len(opening_counts), 1)) if opening_counts else 1
    normalized_entropy = entropy / max(max_entropy, 1e-9)

    author_et_al_frac = author_et_al / total_sentences

    return ProseQualityResult(
        citation_clustering_ratio=round(clustering_ratio, 3),
        multi_cite_fraction=round(multi_cite_frac, 3),
        deep_synthesis_fraction=round(deep_synth_frac, 3),
        surface_comparison_fraction=round(surface_comp_frac, 3),
        single_paper_fraction=round(single_paper_frac, 3),
        opening_entropy=round(normalized_entropy, 3),
        author_et_al_fraction=round(author_et_al_frac, 3),
    )


# ---------------------------------------------------------------------------
# QualityReport and comprehensive_quality_report
# ---------------------------------------------------------------------------

# Composite weights — must sum to 1.0
_WEIGHTS: dict[str, float] = {
    "prose_quality": 0.20,  # captures what the PI sees immediately
    "semantic_coverage": 0.06,
    "centroid_alignment": 0.06,
    "frontier_shift": 0.10,
    "bridge_vectors": 0.11,  # raised: best predictor of cross-community synthesis quality
    "semantic_residual": 0.08,
    "gap_detection": 0.12,
    "argumentative_coherence": 0.10,
    "topic_coverage": 0.05,  # lowered: noisy vocabulary reduces signal-to-noise
    "factual_specificity": 0.12,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"


@dataclass
class QualityReport:
    """Full quality analysis of a literature review.

    Prose quality and factual specificity are always computed. Corpus-dependent
    metrics are Optional and None when the corpus is unavailable.
    ``composite_score()`` re-normalizes weights to those metrics that are present.
    """

    # Always computed (no corpus needed)
    prose_quality: ProseQualityResult
    factual_specificity: FactualSpecificityResult

    # Require corpus + embeddings
    semantic_coverage: Optional[float] = None  # fraction of corpus chunks covered
    centroid_alignment: Optional[float] = None  # cosine sim between review and corpus centroids
    frontier_shift: Optional[FrontierShiftResult] = None
    bridge_vectors: Optional[BridgeVectorResult] = None
    semantic_residual: Optional[SemanticResidualResult] = None
    gap_detection: Optional[GapDetectionResult] = None
    argumentative_coherence: Optional[ArgumentativeCoherenceResult] = None
    topic_coverage: Optional[TopicCoverageResult] = None

    corpus_error: Optional[str] = None

    def composite_score(self) -> float:
        """Weighted composite score in [0, 1].

        Weights are re-normalized to whatever metrics are available,
        so a missing corpus does not artificially deflate the score.
        """
        available: dict[str, float] = {
            "prose_quality": self.prose_quality.score(),
            "factual_specificity": self.factual_specificity.score(),
        }
        if self.semantic_coverage is not None:
            available["semantic_coverage"] = min(self.semantic_coverage, 1.0)
        if self.centroid_alignment is not None:
            available["centroid_alignment"] = max(0.0, self.centroid_alignment)
        if self.frontier_shift is not None:
            available["frontier_shift"] = self.frontier_shift.score()
        if self.bridge_vectors is not None:
            available["bridge_vectors"] = self.bridge_vectors.score()
        if self.semantic_residual is not None:
            available["semantic_residual"] = self.semantic_residual.score()
        if self.gap_detection is not None:
            available["gap_detection"] = self.gap_detection.score()
        if self.argumentative_coherence is not None:
            available["argumentative_coherence"] = self.argumentative_coherence.score()
        if self.topic_coverage is not None:
            available["topic_coverage"] = self.topic_coverage.score()

        total_weight = sum(_WEIGHTS[k] for k in available)
        if total_weight == 0:
            return 0.0
        return sum(available[k] * _WEIGHTS[k] for k in available) / total_weight

    def summary(self) -> str:
        """Human-readable multi-section quality report."""
        lines = [
            "=" * 60,
            "COMPREHENSIVE REVIEW QUALITY REPORT",
            "=" * 60,
        ]

        lines += ["", self.prose_quality.interpretation()]

        if self.semantic_coverage is not None:
            lines += [
                "",
                f"Semantic Coverage: {self.semantic_coverage:.1%}",
                "  Fraction of corpus chunks with a nearby review counterpart.",
            ]
        if self.centroid_alignment is not None:
            lines += [
                "",
                f"Centroid Alignment: {self.centroid_alignment:.3f}",
                "  Cosine similarity between review and corpus centroids.",
            ]
        if self.frontier_shift is not None:
            lines += ["", self.frontier_shift.interpretation()]
        if self.bridge_vectors is not None:
            lines += ["", self.bridge_vectors.interpretation()]
        if self.semantic_residual is not None:
            lines += ["", self.semantic_residual.interpretation()]
        if self.gap_detection is not None:
            lines += ["", self.gap_detection.interpretation()]
        if self.argumentative_coherence is not None:
            lines += ["", self.argumentative_coherence.interpretation()]
        if self.topic_coverage is not None:
            lines += ["", self.topic_coverage.interpretation()]

        lines += ["", self.factual_specificity.interpretation()]

        if self.corpus_error:
            lines += ["", f"[Corpus unavailable: {self.corpus_error}]"]

        lines += [
            "",
            "=" * 60,
            f"COMPOSITE QUALITY SCORE: {self.composite_score():.3f} / 1.000",
            "=" * 60,
        ]
        return "\n".join(lines)


def comprehensive_quality_report(review_text: str) -> QualityReport:
    """Run all 8 quality metrics on a review and return a structured report.

    Builds ``EmbeddingContext`` once (the only expensive step: corpus lookup
    from ChromaDB + ONNX encoding of ~20 review chunks), then passes it to
    every embedding-dependent metric.  Total time with pre-computed embeddings
    is <10s for a typical ALD corpus.

    Args:
        review_text: Full review text (markdown or plain text).

    Returns:
        QualityReport with all available metrics populated.
    """
    # --- Always-available metric (no corpus needed) --------------------------
    prose = compute_prose_quality(review_text)
    factual = compute_factual_specificity(review_text)

    # --- Topic coverage (SQLite only, no embeddings) -------------------------
    topics: Optional[TopicCoverageResult] = None
    try:
        topics = compute_topic_coverage(review_text)
    except Exception as exc:
        corpus_error_topics = str(exc)
    else:
        corpus_error_topics = None

    # --- Build shared embedding context (one-time expensive step) ------------
    corpus_error: Optional[str] = corpus_error_topics
    ctx: Optional[EmbeddingContext] = None
    try:
        ctx = _build_embedding_context(review_text)
    except Exception as exc:
        corpus_error = str(exc)

    if ctx is None:
        return QualityReport(
            prose_quality=prose,
            factual_specificity=factual,
            topic_coverage=topics,
            corpus_error=corpus_error or "Corpus unavailable or empty",
        )

    # --- Semantic coverage + centroid alignment (from shared context, cheap) ---
    coverage_ratio: Optional[float] = None
    centroid_align: Optional[float] = None
    try:
        sim_matrix = ctx.review_embs @ ctx.corpus_embs.T
        nearest_sims = np.max(sim_matrix, axis=0)  # per corpus chunk, nearest review chunk
        coverage_ratio = float(np.mean(nearest_sims > 0.5))

        # Centroid alignment
        corpus_centroid = np.mean(ctx.corpus_embs, axis=0)
        corpus_centroid /= np.linalg.norm(corpus_centroid) + 1e-9
        review_centroid = np.mean(ctx.review_embs, axis=0)
        review_centroid /= np.linalg.norm(review_centroid) + 1e-9
        centroid_align = float(np.dot(corpus_centroid, review_centroid))
    except Exception:  # noqa: BLE001
        pass

    # --- Embedding-dependent metrics (all receive the same ctx) --------------
    frontier = compute_frontier_shift(ctx)
    coherence = compute_argumentative_coherence(ctx)
    residual = compute_semantic_residual(ctx)
    bridges = compute_bridge_vectors(ctx)
    gaps = compute_gap_detection(ctx, review_text)

    return QualityReport(
        prose_quality=prose,
        factual_specificity=factual,
        semantic_coverage=coverage_ratio,
        centroid_alignment=centroid_align,
        frontier_shift=frontier,
        bridge_vectors=bridges,
        semantic_residual=residual,
        gap_detection=gaps,
        argumentative_coherence=coherence,
        topic_coverage=topics,
        corpus_error=corpus_error,
    )
