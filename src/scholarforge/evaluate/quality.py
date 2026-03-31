"""Complementary quality metrics for review evaluation.

These metrics capture what the coverage metric (cosine similarity of chunk
embeddings) misses:

  1. InformationDensity  -- gzip-based Kolmogorov complexity proxy
  2. FactualSpecificity  -- quantitative claims, chemical formulas, comparisons
  3. SemanticEfficiency  -- coverage per word (requires corpus)
  4. CrossReferenceDensity -- distinct papers semantically touched (requires corpus)

Each metric function returns a float score and a human-readable interpretation.
``comprehensive_quality_report`` aggregates all four plus the existing coverage
metric into a single structured report.

Corpus-dependent metrics (3 and 4) are optional: they return None when the
corpus is unavailable or unreachable, rather than crashing.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Optional

# ── Metric 1: Information Density ─────────────────────────────────────────────


@dataclass
class InformationDensityResult:
    """Gzip-based information density analysis."""

    review_compression_ratio: float  # K(review) = len(gzip(review)) / len(review)
    corpus_compression_ratio: float  # K(corpus) = len(gzip(corpus)) / len(corpus)
    density_ratio: float  # K(review) / K(corpus)
    conditional_bits: int  # len(gzip(corpus + review)) - len(gzip(corpus))
    review_byte_len: int
    corpus_byte_len: int

    def score(self) -> float:
        """Composite score in [0, 1].

        High density ratio (review is as dense as corpus) and low conditional
        bits (review is redundant with corpus, not novel) are both desirable.
        Returns a value that rewards both properties.
        """
        # density_ratio close to 1 is ideal; cap to avoid unbounded values
        density_score = min(self.density_ratio, 1.0)
        # conditional_bits normalised by review length: lower = better
        bits_per_byte = self.conditional_bits / max(self.review_byte_len, 1)
        bits_score = max(0.0, 1.0 - bits_per_byte)
        return 0.5 * density_score + 0.5 * bits_score

    def interpretation(self) -> str:
        lines = [
            "Information Density (gzip proxy):",
            f"  Review compression ratio K(review): {self.review_compression_ratio:.3f}",
            f"  Corpus compression ratio K(corpus): {self.corpus_compression_ratio:.3f}",
            f"  Density ratio K(review)/K(corpus): {self.density_ratio:.3f}",
            f"  Conditional bits (new info added by review): {self.conditional_bits:,}",
            f"  Composite score: {self.score():.3f}",
        ]
        if self.density_ratio < 0.7:
            lines.append(
                "  Interpretation: Review is much more compressible than the corpus "
                "-- may contain filler or repetitive phrasing."
            )
        elif self.density_ratio >= 0.95:
            lines.append(
                "  Interpretation: Review density matches or exceeds corpus density "
                "-- language is tight and information-rich."
            )
        else:
            lines.append(
                "  Interpretation: Review density is moderately close to corpus "
                "-- acceptable but may have some redundancy."
            )
        return "\n".join(lines)


def compute_information_density(
    review_text: str,
    corpus_text: str,
) -> InformationDensityResult:
    """Compute gzip-based information density for a review against a corpus.

    Uses zlib.compress (no gzip header overhead) for more stable ratios on
    short inputs.  The concatenation trick for conditional compression is a
    standard practical proxy for K(review | corpus).

    Args:
        review_text: The full review text.
        corpus_text: The concatenated corpus text (all chunk content joined).
            Can be a representative sample if the full corpus is very large.

    Returns:
        InformationDensityResult with all sub-metrics.
    """
    # Use zlib (level 9) to avoid gzip header overhead on short inputs
    review_bytes = review_text.encode("utf-8", errors="replace")
    corpus_bytes = corpus_text.encode("utf-8", errors="replace")
    combined_bytes = corpus_bytes + b"\n\n" + review_bytes

    review_len = len(review_bytes)
    corpus_len = len(corpus_bytes)

    # Compression ratios (compressed / original)
    review_compressed = len(zlib.compress(review_bytes, level=9))
    corpus_compressed = len(zlib.compress(corpus_bytes, level=9))
    combined_compressed = len(zlib.compress(combined_bytes, level=9))

    review_ratio = review_compressed / max(review_len, 1)
    corpus_ratio = corpus_compressed / max(corpus_len, 1)
    density_ratio = review_ratio / max(corpus_ratio, 1e-9)

    # Conditional compression: how many bytes the review adds to an
    # already-compressed corpus representation.  Low means the review is
    # largely redundant with (covered by) the corpus -- desirable.
    conditional_bits = combined_compressed - corpus_compressed

    return InformationDensityResult(
        review_compression_ratio=review_ratio,
        corpus_compression_ratio=corpus_ratio,
        density_ratio=density_ratio,
        conditional_bits=conditional_bits,
        review_byte_len=review_len,
        corpus_byte_len=corpus_len,
    )


# ── Metric 2: Factual Specificity ─────────────────────────────────────────────

# ALD-domain unit patterns (both plain text and Unicode superscripts)
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

# Chemical formula: one or two capital letters + optional digits, repeated 2+
# Matches HfO2, Al2O3, TiN, SrTiO3, Ta2O5, ZrO2, MoS2, etc.
# Also handles Unicode subscripts: HfO₂
_CHEM_FORMULA = re.compile(r"\b(?:[A-Z][a-z]?\d*[₀₁₂₃₄₅₆₇₈₉]*){2,}\b")

# Author-style named entities: "Lastname et al." patterns
_AUTHOR_ET_AL = re.compile(r"\b[A-Z][a-z]+ et al\.", re.IGNORECASE)

# Comparative statements: sentences containing at least two numeric values
# (rough proxy for "X achieved Y while Z achieved W")
_COMPARATIVE = re.compile(
    r"[^.!?]*\b\d+(?:\.\d+)?\s*[a-zA-Z%°]*[^.!?]*\b\d+(?:\.\d+)?\s*[a-zA-Z%°]*[^.!?]*[.!?]"
)

# Device/material proper nouns (acronyms used in the field)
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
        """Normalized specificity score per 1,000 words.

        Uses log scaling so high-specificity reviews are differentiated
        rather than all capping at 1.0.
        """
        if self.word_count == 0:
            return 0.0
        import math

        raw = (
            self.numeric_with_units * 2.0
            + self.chemical_formulas * 1.5
            + self.author_citations * 1.0
            + self.comparative_sentences * 1.5
            + self.technical_acronyms * 0.5
        )
        per_1k = raw / max(self.word_count, 1) * 1000
        # Log scale: score = log(1 + per_1k) / log(1 + 80)
        # At 10 markers/1kw: 0.53, at 20: 0.69, at 40: 0.84, at 80: 1.0
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
            f"  Comparative sentences (2+ numbers): {self.comparative_sentences}",
            f"  Technical acronyms: {self.technical_acronyms}",
            f"  Composite score: {self.score():.3f}",
        ]
        s = self.score()
        if s < 0.2:
            lines.append("  Interpretation: Low specificity -- review is vague or general.")
        elif s < 0.5:
            lines.append(
                "  Interpretation: Moderate specificity -- review includes some data "
                "but lacks quantitative depth."
            )
        else:
            lines.append(
                "  Interpretation: High specificity -- review is data-rich with "
                "quantitative claims and named entities."
            )
        return "\n".join(lines)


def compute_factual_specificity(review_text: str) -> FactualSpecificityResult:
    """Count factual markers (numbers + units, formulas, citations, comparisons).

    Args:
        review_text: The full review text (markdown or plain text).

    Returns:
        FactualSpecificityResult with per-category counts and a composite score.
    """
    # Strip markdown headings for cleaner counting
    body = re.sub(r"^#+\s.*$", "", review_text, flags=re.MULTILINE)
    # Strip URLs / links
    body = re.sub(r"\[.*?\]\(.*?\)", "", body)

    word_count = len(body.split())

    # Deduplicate chemical formula matches to avoid counting HfO2 50 times
    chem_matches = set(_CHEM_FORMULA.findall(body))
    # Remove single-word false positives (e.g. "I", "A")
    chem_matches = {m for m in chem_matches if len(m) >= 3}

    return FactualSpecificityResult(
        numeric_with_units=len(_UNIT_PATTERN.findall(body)),
        chemical_formulas=len(chem_matches),
        author_citations=len(_AUTHOR_ET_AL.findall(body)),
        comparative_sentences=len(_COMPARATIVE.findall(body)),
        technical_acronyms=len(set(_ACRONYMS.findall(body))),
        word_count=word_count,
    )


# ── Metric 3: Semantic Compression Efficiency ──────────────────────────────────


@dataclass
class SemanticEfficiencyResult:
    """Semantic coverage per 1,000 words."""

    coverage_ratio: float
    word_count: int
    efficiency: float  # coverage_ratio / (word_count / 1000)

    def score(self) -> float:
        """Normalised score.  Cap at 1.0 (efficiency of 1.0 coverage per 1k words)."""
        return min(self.efficiency, 1.0)

    def interpretation(self) -> str:
        lines = [
            "Semantic Compression Efficiency:",
            f"  Coverage ratio: {self.coverage_ratio:.1%}",
            f"  Word count: {self.word_count:,}",
            f"  Efficiency (coverage / 1k words): {self.efficiency:.4f}",
            f"  Score: {self.score():.3f}",
        ]
        if self.efficiency < 0.05:
            lines.append(
                "  Interpretation: Low efficiency -- review uses many words "
                "to cover little corpus content."
            )
        elif self.efficiency < 0.15:
            lines.append(
                "  Interpretation: Moderate efficiency -- decent coverage relative to length."
            )
        else:
            lines.append(
                "  Interpretation: High efficiency -- review achieves broad "
                "coverage with concise language."
            )
        return "\n".join(lines)


def compute_semantic_efficiency(
    review_text: str,
    coverage_ratio: Optional[float] = None,
    threshold: float = 0.5,
) -> Optional[SemanticEfficiencyResult]:
    """Compute semantic coverage per 1,000 words.

    Args:
        review_text: The full review text.
        coverage_ratio: Pre-computed coverage ratio.  If None, calls
            ``compute_coverage`` internally (requires corpus).
        threshold: Coverage distance threshold (passed to compute_coverage
            if coverage_ratio is not supplied).

    Returns:
        SemanticEfficiencyResult, or None if coverage cannot be computed.
    """
    if coverage_ratio is None:
        try:
            from scholarforge.evaluate.coverage import compute_coverage

            result = compute_coverage(review_text, threshold=threshold)
            coverage_ratio = result.coverage_ratio
        except Exception:  # noqa: BLE001
            return None

    word_count = len(review_text.split())
    if word_count == 0:
        return None

    efficiency = coverage_ratio / (word_count / 1000.0)

    return SemanticEfficiencyResult(
        coverage_ratio=coverage_ratio,
        word_count=word_count,
        efficiency=efficiency,
    )


# ── Metric 4: Cross-Reference Density ─────────────────────────────────────────


@dataclass
class CrossReferenceDensityResult:
    """How many distinct corpus papers are semantically touched by the review."""

    touched_papers: int
    total_papers: int
    density: float  # touched / total
    threshold: float
    touched_paper_ids: list[str] = field(default_factory=list)

    def score(self) -> float:
        return self.density

    def interpretation(self) -> str:
        lines = [
            "Cross-Reference Density:",
            f"  Touched papers: {self.touched_papers} / {self.total_papers}",
            f"  Density: {self.density:.1%}",
            f"  Semantic proximity threshold (cosine distance): {self.threshold}",
            f"  Score: {self.score():.3f}",
        ]
        if self.density < 0.3:
            lines.append(
                "  Interpretation: Review only touches a small fraction of corpus "
                "-- narrow or incomplete coverage."
            )
        elif self.density < 0.6:
            lines.append(
                "  Interpretation: Review touches a moderate fraction of corpus -- decent breadth."
            )
        else:
            lines.append(
                "  Interpretation: Review touches most of the corpus papers "
                "-- broad, well-rounded coverage."
            )
        return "\n".join(lines)


def compute_cross_reference_density(
    review_text: str,
    threshold: float = 0.5,
    chunk_size: int = 200,
) -> Optional[CrossReferenceDensityResult]:
    """Count distinct corpus papers semantically proximate to the review.

    A paper is "touched" if at least one of its corpus chunks has a cosine
    distance below ``threshold`` to any review chunk.  This is a breadth
    metric: it measures how many different papers the review draws from, not
    just overall coverage.

    Args:
        review_text: The full review text.
        threshold: Cosine distance threshold (same scale as compute_coverage).
        chunk_size: Words per review chunk.

    Returns:
        CrossReferenceDensityResult, or None if corpus is unavailable.
    """
    import re as _re

    try:
        import numpy as np

        from scholarforge.evaluate.coverage import load_corpus_chunks
        from scholarforge.store.embeddings import _store, get_chunk_embeddings
    except Exception:  # noqa: BLE001
        return None

    try:
        chunks = load_corpus_chunks()
    except Exception:  # noqa: BLE001
        return None

    if not chunks:
        return CrossReferenceDensityResult(
            touched_papers=0,
            total_papers=0,
            density=0.0,
            threshold=threshold,
        )

    # Get corpus chunk embeddings
    all_ids = [c.id for c in chunks]
    stored = get_chunk_embeddings(all_ids)

    if not stored:
        return None

    # Build per-paper arrays
    paper_chunk_embs: dict[str, list] = {}
    for c in chunks:
        emb = stored.get(c.id)
        if emb is not None:
            paper_chunk_embs.setdefault(c.paper_id, []).append(emb)

    total_papers = len(paper_chunk_embs)
    if total_papers == 0:
        return CrossReferenceDensityResult(
            touched_papers=0,
            total_papers=0,
            density=0.0,
            threshold=threshold,
        )

    # Embed review chunks
    review_body = _re.split(r"\n## References\n", review_text)[0]
    review_body = _re.sub(r"^#+.*$", "", review_body, flags=_re.MULTILINE).strip()
    words = review_body.split()
    review_chunks = [
        " ".join(words[i : i + chunk_size])
        for i in range(0, len(words), chunk_size)
        if len(" ".join(words[i : i + chunk_size]).strip()) > 50
    ]

    if not review_chunks:
        return CrossReferenceDensityResult(
            touched_papers=0,
            total_papers=total_papers,
            density=0.0,
            threshold=threshold,
        )

    model = _store.model
    rev_embs = np.array(model.encode(review_chunks, show_progress_bar=False, batch_size=64))
    rev_norms = np.linalg.norm(rev_embs, axis=1, keepdims=True)
    rev_norms[rev_norms == 0] = 1
    rev_embs = rev_embs / rev_norms  # (n_review, 384) normalised

    # For each paper, check if any of its chunks is within threshold of any review chunk
    touched: list[str] = []
    for paper_id, embs in paper_chunk_embs.items():
        paper_embs = np.array(embs)
        p_norms = np.linalg.norm(paper_embs, axis=1, keepdims=True)
        p_norms[p_norms == 0] = 1
        paper_embs = paper_embs / p_norms  # (n_paper_chunks, 384) normalised

        # (n_paper_chunks, n_review) similarity matrix
        sim = paper_embs @ rev_embs.T
        # Closest review chunk to any paper chunk
        best_distance = float(1.0 - np.max(sim))
        if best_distance < threshold:
            touched.append(paper_id)

    density = len(touched) / total_papers

    return CrossReferenceDensityResult(
        touched_papers=len(touched),
        total_papers=total_papers,
        density=density,
        threshold=threshold,
        touched_paper_ids=touched,
    )


# ── Metric 5: Thematic Centroid Correlation ──────────────────────────────────


@dataclass
class ThematicCentroidResult:
    """Measures alignment between review's semantic center and corpus's center."""

    cosine_similarity: float  # review centroid vs corpus centroid
    thematic_drift: float  # 1 - similarity (0 = perfect alignment)

    def score(self) -> float:
        """Score: high similarity = good alignment."""
        return max(0.0, self.cosine_similarity)

    def interpretation(self) -> str:
        lines = [
            "Thematic Centroid Correlation:",
            f"  Cosine similarity (review vs corpus center): {self.cosine_similarity:.3f}",
            f"  Thematic drift: {self.thematic_drift:.3f}",
            f"  Score: {self.score():.3f}",
        ]
        if self.thematic_drift < 0.15:
            lines.append("  Interpretation: Review is tightly aligned with corpus themes.")
        elif self.thematic_drift < 0.30:
            lines.append("  Interpretation: Review is well-aligned with moderate drift.")
        else:
            lines.append(
                "  Interpretation: Significant thematic drift -- review may focus on a niche."
            )
        return "\n".join(lines)


def compute_thematic_centroid(review_text: str) -> Optional[ThematicCentroidResult]:
    """Compare the semantic centroid of the review to the corpus centroid.

    Both centroids are weighted averages of chunk embeddings. A high cosine
    similarity means the review's "center of gravity" matches the corpus.
    Low similarity indicates thematic drift (the review focuses on a niche
    rather than the corpus as a whole).
    """
    try:
        import numpy as np

        from scholarforge.evaluate.coverage import load_corpus_chunks
        from scholarforge.store.embeddings import _store, get_chunk_embeddings
    except Exception:  # noqa: BLE001
        return None

    try:
        chunks = load_corpus_chunks()
        if not chunks:
            return None

        # Corpus centroid from stored embeddings
        all_ids = [c.id for c in chunks]
        stored = get_chunk_embeddings(all_ids)
        corpus_embs = [stored[c.id] for c in chunks if c.id in stored]
        if not corpus_embs:
            return None
        corpus_weights = [c.token_count for c in chunks if c.id in stored]

        corpus_arr = np.array(corpus_embs)
        w = np.array(corpus_weights, dtype=float)
        w /= w.sum() + 1e-9
        corpus_centroid = np.average(corpus_arr, axis=0, weights=w)
        corpus_centroid /= np.linalg.norm(corpus_centroid) + 1e-9

        # Review centroid from on-the-fly encoding
        review_body = re.split(r"\n## References\n", review_text)[0]
        review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()
        words = review_body.split()
        review_chunks = [
            " ".join(words[i : i + 200])
            for i in range(0, len(words), 200)
            if len(" ".join(words[i : i + 200]).strip()) > 50
        ]
        if not review_chunks:
            return None

        model = _store.model
        rev_embs = model.encode(review_chunks, show_progress_bar=False, batch_size=64)
        rev_centroid = np.mean(rev_embs, axis=0)
        rev_centroid /= np.linalg.norm(rev_centroid) + 1e-9

        sim = float(np.dot(corpus_centroid, rev_centroid))
        return ThematicCentroidResult(cosine_similarity=sim, thematic_drift=1.0 - sim)
    except Exception:  # noqa: BLE001
        return None


# ── Metric 6: Topic Coverage Gap Analysis ────────────────────────────────────


@dataclass
class TopicCoverageResult:
    """Topic-level coverage analysis — which corpus topics appear in the review."""

    topics_in_corpus: int
    topics_covered: int
    topics_omitted: list[str]
    coverage_ratio: float
    topic_detail: dict[str, str]  # topic -> "covered" | "mentioned" | "omitted"

    def score(self) -> float:
        return self.coverage_ratio

    def interpretation(self) -> str:
        lines = [
            "Topic Coverage Gap Analysis:",
            f"  Corpus topics: {self.topics_in_corpus}",
            f"  Topics covered in review: {self.topics_covered}",
            f"  Coverage ratio: {self.coverage_ratio:.1%}",
            f"  Score: {self.score():.3f}",
        ]
        if self.topics_omitted:
            lines.append(f"  Omitted topics: {', '.join(self.topics_omitted[:10])}")
        return "\n".join(lines)


def compute_topic_coverage(review_text: str) -> Optional[TopicCoverageResult]:
    """Check which corpus topics appear in the review text.

    Uses the extracted topic vocabulary from the corpus (PaperTopic table)
    and checks for case-insensitive substring matches in the review.
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import PaperTopic
    except Exception:  # noqa: BLE001
        return None

    try:
        with get_session() as session:
            all_topics = session.exec(select(PaperTopic)).all()

        # Get unique topic names, filter junk (HTML artifacts, too short, too long)
        topic_names = sorted(
            {
                t.topic
                for t in all_topics
                if len(t.topic) >= 3
                and len(t.topic) <= 60
                and "<" not in t.topic
                and "|" not in t.topic
                and "." not in t.topic[:3]  # skip numbered sections
            }
        )
        if not topic_names:
            return None

        review_lower = review_text.lower()
        detail = {}
        covered = 0
        omitted = []

        for topic in topic_names:
            topic_lower = topic.lower()
            if topic_lower in review_lower:
                # Check if it's substantially mentioned (>1 occurrence or >3 words around it)
                count = review_lower.count(topic_lower)
                if count >= 2:
                    detail[topic] = "covered"
                    covered += 1
                else:
                    detail[topic] = "mentioned"
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
    except Exception:  # noqa: BLE001
        return None


# ── Metric 7: Reconstruction Fidelity (Compression Quality) ─────────────────


@dataclass
class ReconstructionFidelityResult:
    """Measures how well the review can 'reconstruct' corpus findings.

    Uses the normalized compression distance (NCD) between review and corpus
    as a proxy for mutual information. Lower NCD = review captures more of
    the corpus's information content.
    """

    ncd: float  # Normalized compression distance [0, 1]
    review_self_info: int  # compressed size of review alone
    corpus_self_info: int  # compressed size of corpus sample alone
    joint_info: int  # compressed size of concatenation

    def score(self) -> float:
        """Score: 1 - NCD. Higher = better reconstruction fidelity."""
        return max(0.0, min(1.0, 1.0 - self.ncd))

    def interpretation(self) -> str:
        lines = [
            "Reconstruction Fidelity (NCD proxy):",
            f"  Normalized Compression Distance: {self.ncd:.3f}",
            f"  Review self-info: {self.review_self_info:,} bytes",
            f"  Corpus self-info: {self.corpus_self_info:,} bytes",
            f"  Joint info: {self.joint_info:,} bytes",
            f"  Score (1 - NCD): {self.score():.3f}",
        ]
        if self.ncd < 0.7:
            lines.append(
                "  Interpretation: High fidelity -- review captures substantial "
                "corpus information (low NCD)."
            )
        elif self.ncd < 0.85:
            lines.append(
                "  Interpretation: Moderate fidelity -- review shares some information with corpus."
            )
        else:
            lines.append(
                "  Interpretation: Low fidelity -- review and corpus share "
                "little compressed information."
            )
        return "\n".join(lines)


def compute_reconstruction_fidelity(
    review_text: str,
    corpus_text: str,
) -> ReconstructionFidelityResult:
    """Compute Normalized Compression Distance between review and corpus.

    NCD(x, y) = (C(xy) - min(C(x), C(y))) / max(C(x), C(y))

    To handle size asymmetry (3KB review vs 170KB+ corpus), we sample
    the corpus to be within 3x of the review length. This makes NCD
    comparable across reviews of different lengths.
    """
    review_bytes = review_text.encode("utf-8", errors="replace")

    # Sample corpus to be within 3x of review length for fair NCD
    corpus_bytes_full = corpus_text.encode("utf-8", errors="replace")
    max_corpus_len = len(review_bytes) * 3
    if len(corpus_bytes_full) > max_corpus_len:
        corpus_bytes = corpus_bytes_full[:max_corpus_len]
    else:
        corpus_bytes = corpus_bytes_full

    combined = review_bytes + b"\n\n" + corpus_bytes

    c_review = len(zlib.compress(review_bytes, level=9))
    c_corpus = len(zlib.compress(corpus_bytes, level=9))
    c_combined = len(zlib.compress(combined, level=9))

    ncd = (c_combined - min(c_review, c_corpus)) / max(c_review, c_corpus, 1)

    return ReconstructionFidelityResult(
        ncd=ncd,
        review_self_info=c_review,
        corpus_self_info=c_corpus,
        joint_info=c_combined,
    )


# ── Metric 8: Semantic Span (Convex Hull Volume Ratio) ───────────────────────


@dataclass
class SemanticSpanResult:
    """Measures whether the review spans the same semantic volume as the corpus.

    Projects embeddings to a low-dimensional space (PCA), computes convex hull
    volumes for both corpus and review, and reports the ratio. Also measures
    the Hausdorff distance (worst-case gap between the two point clouds).
    """

    corpus_volume: float  # convex hull volume of corpus in PCA space
    review_volume: float  # convex hull volume of review in PCA space
    volume_ratio: float  # review / corpus (1.0 = same span)
    hausdorff_distance: float  # max distance from any corpus point to nearest review point
    pca_dims: int  # number of PCA dimensions used

    def score(self) -> float:
        """Score based on volume ratio and Hausdorff distance.

        Volume ratio near 1.0 is ideal. Hausdorff penalizes reviews that
        miss entire regions of the corpus space.
        """
        # Volume component: penalize both too small (narrow) and too large (hallucinating)
        vol_score = min(self.volume_ratio, 1.0)
        # Hausdorff component: lower is better (inverse, capped)
        haus_score = max(0.0, 1.0 - self.hausdorff_distance / 2.0)
        return 0.5 * vol_score + 0.5 * haus_score

    def interpretation(self) -> str:
        lines = [
            "Semantic Span (Convex Hull in PCA space):",
            f"  PCA dimensions: {self.pca_dims}",
            f"  Corpus hull volume: {self.corpus_volume:.4f}",
            f"  Review hull volume: {self.review_volume:.4f}",
            f"  Volume ratio (review/corpus): {self.volume_ratio:.3f}",
            f"  Hausdorff distance: {self.hausdorff_distance:.3f}",
            f"  Score: {self.score():.3f}",
        ]
        if self.volume_ratio < 0.3:
            lines.append("  Interpretation: Review covers a narrow slice of the corpus space.")
        elif self.volume_ratio < 0.7:
            lines.append("  Interpretation: Review spans a moderate portion of corpus space.")
        else:
            lines.append("  Interpretation: Review spans most of the corpus semantic space.")
        return "\n".join(lines)


def compute_semantic_span(
    review_text: str,
    n_components: int = 5,
    chunk_size: int = 200,
) -> Optional[SemanticSpanResult]:
    """Compare convex hull volumes of review vs corpus in PCA-projected space.

    Projects both corpus and review chunk embeddings into a shared PCA space,
    then computes convex hull volumes and Hausdorff distance.

    Args:
        review_text: The full review text.
        n_components: PCA dimensions (default 5; higher = more precise but
            convex hull computation is exponential in dimensions).
        chunk_size: Words per review chunk.
    """
    try:
        import numpy as np
        from scipy.spatial import ConvexHull
        from sklearn.decomposition import PCA

        from scholarforge.evaluate.coverage import load_corpus_chunks
        from scholarforge.store.embeddings import _store, get_chunk_embeddings
    except Exception:  # noqa: BLE001
        return None

    try:
        chunks = load_corpus_chunks()
        if not chunks:
            return None

        # Get corpus embeddings
        all_ids = [c.id for c in chunks]
        stored = get_chunk_embeddings(all_ids)
        corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
        if len(corpus_embs) < n_components + 1:
            return None

        # Embed review chunks
        review_body = re.split(r"\n## References\n", review_text)[0]
        review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()
        words = review_body.split()
        review_chunks = [
            " ".join(words[i : i + chunk_size])
            for i in range(0, len(words), chunk_size)
            if len(" ".join(words[i : i + chunk_size]).strip()) > 50
        ]
        if len(review_chunks) < n_components + 1:
            return None

        model = _store.model
        review_embs = np.array(model.encode(review_chunks, show_progress_bar=False, batch_size=64))

        # PCA: fit on corpus, transform both
        pca = PCA(n_components=n_components)
        corpus_pca = pca.fit_transform(corpus_embs)
        review_pca = pca.transform(review_embs)

        # Convex hull volumes
        try:
            corpus_hull = ConvexHull(corpus_pca)
            corpus_vol = corpus_hull.volume
        except Exception:  # noqa: BLE001
            corpus_vol = 0.0

        try:
            review_hull = ConvexHull(review_pca)
            review_vol = review_hull.volume
        except Exception:  # noqa: BLE001
            review_vol = 0.0

        vol_ratio = review_vol / max(corpus_vol, 1e-12)

        # Hausdorff distance: max over corpus points of min distance to review
        from scipy.spatial.distance import cdist

        dists = cdist(corpus_pca, review_pca)
        hausdorff = float(np.max(np.min(dists, axis=1)))

        return SemanticSpanResult(
            corpus_volume=corpus_vol,
            review_volume=review_vol,
            volume_ratio=vol_ratio,
            hausdorff_distance=hausdorff,
            pca_dims=n_components,
        )
    except Exception:  # noqa: BLE001
        return None


# ── Metric 9: Argumentative Coherence ────────────────────────────────────────


@dataclass
class ArgumentativeCoherenceResult:
    """Measures whether the review preserves causal/logical chains from the corpus.

    In the corpus, consecutive chunks within a paper form an argumentative
    chain (chunk_i argues/leads-to chunk_{i+1}). If the review covers both,
    this metric checks whether the matching review positions are also nearby,
    preserving the logical flow.

    Additionally measures "citation graph coherence": when the corpus has
    paper A citing paper B, the review should discuss B's findings before
    or alongside A's — not scatter them arbitrarily.
    """

    # Chain preservation
    total_chains: int  # consecutive chunk pairs in corpus
    chains_both_covered: int  # both chunks matched by review
    chains_order_preserved: int  # matched AND review positions are sequential
    chain_preservation_ratio: float  # order_preserved / both_covered

    # Citation coherence
    total_citation_edges: int
    citation_edges_both_mentioned: int
    citation_order_preserved: int
    citation_coherence_ratio: float

    def score(self) -> float:
        """Weighted combination of chain preservation and citation coherence."""
        chain = self.chain_preservation_ratio if self.chains_both_covered > 0 else 0.5
        cite = self.citation_coherence_ratio if self.citation_edges_both_mentioned > 0 else 0.5
        return 0.6 * chain + 0.4 * cite

    def interpretation(self) -> str:
        lines = [
            "Argumentative Coherence:",
            f"  Corpus argument chains: {self.total_chains}",
            f"  Chains both covered in review: {self.chains_both_covered}",
            f"  Chains with preserved order: {self.chains_order_preserved}",
            f"  Chain preservation ratio: {self.chain_preservation_ratio:.3f}",
            f"  Citation edges in corpus: {self.total_citation_edges}",
            f"  Citation edges both mentioned: {self.citation_edges_both_mentioned}",
            f"  Citation order preserved: {self.citation_order_preserved}",
            f"  Citation coherence: {self.citation_coherence_ratio:.3f}",
            f"  Score: {self.score():.3f}",
        ]
        s = self.score()
        if s > 0.7:
            lines.append(
                "  Interpretation: Strong coherence -- review preserves logical "
                "flow and citation relationships."
            )
        elif s > 0.4:
            lines.append(
                "  Interpretation: Moderate coherence -- some argumentative "
                "chains are preserved, others scattered."
            )
        else:
            lines.append(
                "  Interpretation: Weak coherence -- review scatters related "
                "ideas without preserving logical connections."
            )
        return "\n".join(lines)


def compute_argumentative_coherence(
    review_text: str,
    proximity_window: int = 3,
    similarity_threshold: float = 0.5,
) -> Optional[ArgumentativeCoherenceResult]:
    """Measure how well the review preserves argumentative chains from the corpus.

    Algorithm:
    1. For each corpus paper, take consecutive chunk pairs (c_i, c_{i+1})
    2. Find the nearest review chunk to each corpus chunk
    3. If both are matched (above similarity threshold), check if the
       matching review chunks are within `proximity_window` positions
       of each other — preserving the sequential relationship
    4. Also check citation graph edges: if paper A cites paper B, and
       the review discusses both, does B's content appear before A's?

    Args:
        review_text: The full review text.
        proximity_window: Max position gap between matched review chunks
            to consider the argument chain "preserved" (default 3).
        similarity_threshold: Min cosine similarity to consider a corpus
            chunk "covered" by a review chunk (default 0.5).
    """
    try:
        import numpy as np

        from scholarforge.evaluate.coverage import load_corpus_chunks
        from scholarforge.store.embeddings import _store, get_chunk_embeddings
    except Exception:  # noqa: BLE001
        return None

    try:
        chunks = load_corpus_chunks()
        if not chunks:
            return None

        # Get corpus chunk embeddings
        all_ids = [c.id for c in chunks]
        stored = get_chunk_embeddings(all_ids)

        # Group chunks by paper, preserving order
        paper_chunks: dict[str, list] = {}
        paper_chunk_embs: dict[str, list] = {}
        for c in chunks:
            emb = stored.get(c.id)
            if emb is not None:
                paper_chunks.setdefault(c.paper_id, []).append(c)
                paper_chunk_embs.setdefault(c.paper_id, []).append(emb)

        # Embed review chunks
        review_body = re.split(r"\n## References\n", review_text)[0]
        review_body = re.sub(r"^#+.*$", "", review_body, flags=re.MULTILINE).strip()
        words = review_body.split()
        review_chunk_texts = [
            " ".join(words[i : i + 150])
            for i in range(0, len(words), 150)
            if len(" ".join(words[i : i + 150]).strip()) > 50
        ]
        if not review_chunk_texts:
            return None

        model = _store.model
        rev_embs = np.array(
            model.encode(review_chunk_texts, show_progress_bar=False, batch_size=64)
        )
        rev_norms = np.linalg.norm(rev_embs, axis=1, keepdims=True)
        rev_norms[rev_norms == 0] = 1
        rev_embs = rev_embs / rev_norms

        # For each corpus chunk, find nearest review chunk position
        total_chains = 0
        chains_both_covered = 0
        chains_order_preserved = 0

        for paper_id, p_embs_list in paper_chunk_embs.items():
            if len(p_embs_list) < 2:
                continue

            p_embs = np.array(p_embs_list)
            p_norms = np.linalg.norm(p_embs, axis=1, keepdims=True)
            p_norms[p_norms == 0] = 1
            p_embs = p_embs / p_norms

            # Similarity matrix: (n_corpus_chunks, n_review_chunks)
            sim_matrix = p_embs @ rev_embs.T

            # For each corpus chunk, best matching review position
            best_review_pos = np.argmax(sim_matrix, axis=1)
            best_review_sim = np.max(sim_matrix, axis=1)

            # Check consecutive pairs
            for i in range(len(p_embs_list) - 1):
                total_chains += 1
                sim_i = best_review_sim[i]
                sim_next = best_review_sim[i + 1]

                if sim_i >= similarity_threshold and sim_next >= similarity_threshold:
                    chains_both_covered += 1
                    pos_i = best_review_pos[i]
                    pos_next = best_review_pos[i + 1]
                    # Check if review positions are nearby AND in order
                    gap = abs(int(pos_next) - int(pos_i))
                    if gap <= proximity_window:
                        chains_order_preserved += 1

        chain_ratio = (
            chains_order_preserved / max(chains_both_covered, 1) if chains_both_covered > 0 else 0.0
        )

        # Citation graph coherence
        total_cite_edges = 0
        cite_both_mentioned = 0
        cite_order_preserved = 0

        try:
            from sqlmodel import select

            from scholarforge.store.db import get_session
            from scholarforge.store.models import Citation

            with get_session() as session:
                citations = session.exec(select(Citation)).all()

            # For each citation edge where both papers are in corpus
            for cit in citations:
                if (
                    cit.cited_paper_id
                    and cit.paper_id in paper_chunk_embs
                    and cit.cited_paper_id in paper_chunk_embs
                ):
                    total_cite_edges += 1

                    # Find average review position for each paper's content
                    citing_embs = np.array(paper_chunk_embs[cit.paper_id])
                    c_norms = np.linalg.norm(citing_embs, axis=1, keepdims=True)
                    c_norms[c_norms == 0] = 1
                    citing_embs = citing_embs / c_norms
                    citing_sims = np.max(citing_embs @ rev_embs.T, axis=1)

                    cited_embs = np.array(paper_chunk_embs[cit.cited_paper_id])
                    cd_norms = np.linalg.norm(cited_embs, axis=1, keepdims=True)
                    cd_norms[cd_norms == 0] = 1
                    cited_embs = cited_embs / cd_norms
                    cited_sims = np.max(cited_embs @ rev_embs.T, axis=1)

                    citing_covered = np.any(citing_sims >= similarity_threshold)
                    cited_covered = np.any(cited_sims >= similarity_threshold)

                    if citing_covered and cited_covered:
                        cite_both_mentioned += 1
                        # Check if cited paper appears before citing paper in review
                        citing_pos = float(np.mean(np.argmax(citing_embs @ rev_embs.T, axis=1)))
                        cited_pos = float(np.mean(np.argmax(cited_embs @ rev_embs.T, axis=1)))
                        # Cited work should appear before or near the citing work
                        if cited_pos <= citing_pos + proximity_window:
                            cite_order_preserved += 1
        except Exception:  # noqa: BLE001
            pass

        cite_ratio = (
            cite_order_preserved / max(cite_both_mentioned, 1) if cite_both_mentioned > 0 else 0.0
        )

        return ArgumentativeCoherenceResult(
            total_chains=total_chains,
            chains_both_covered=chains_both_covered,
            chains_order_preserved=chains_order_preserved,
            chain_preservation_ratio=chain_ratio,
            total_citation_edges=total_cite_edges,
            citation_edges_both_mentioned=cite_both_mentioned,
            citation_order_preserved=cite_order_preserved,
            citation_coherence_ratio=cite_ratio,
        )
    except Exception:  # noqa: BLE001
        return None


# ── Corpus text helper ─────────────────────────────────────────────────────────


def _load_corpus_text(max_chars: int = 500_000) -> Optional[str]:
    """Load corpus chunk content as a single string (for information density).

    Caps at ``max_chars`` to keep compression tractable.  The sample is taken
    from the beginning of the corpus; this is sufficient for ratio estimation.
    """
    try:
        from scholarforge.evaluate.coverage import load_corpus_chunks

        chunks = load_corpus_chunks()
    except Exception:  # noqa: BLE001
        return None

    if not chunks:
        return None

    parts: list[str] = []
    total = 0
    for c in chunks:
        parts.append(c.content)
        total += len(c.content)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


# ── Comprehensive quality report ───────────────────────────────────────────────


@dataclass
class QualityReport:
    """Full quality analysis of a review."""

    # Always computed (no corpus needed)
    information_density: InformationDensityResult
    factual_specificity: FactualSpecificityResult

    # Require corpus access
    coverage_ratio: Optional[float] = None
    semantic_efficiency: Optional[SemanticEfficiencyResult] = None
    cross_reference_density: Optional[CrossReferenceDensityResult] = None
    thematic_centroid: Optional[ThematicCentroidResult] = None
    topic_coverage: Optional[TopicCoverageResult] = None
    reconstruction_fidelity: Optional[ReconstructionFidelityResult] = None
    semantic_span: Optional[SemanticSpanResult] = None
    argumentative_coherence: Optional[ArgumentativeCoherenceResult] = None

    # Error info for corpus-dependent failures
    corpus_error: Optional[str] = None

    def composite_score(self) -> float:
        """Weighted average of all available sub-scores.

        Weights chosen so no single metric dominates:
          - Thematic centroid:       0.15 (alignment check)
          - Topic coverage:          0.15 (breadth check)
          - Reconstruction fidelity: 0.15 (compression quality)
          - Factual specificity:     0.15 (data richness)
          - Semantic coverage:       0.15 (chunk-level coverage)
          - Semantic efficiency:     0.10 (coverage per word)
          - Information density:     0.10 (compression ratio)
          - Cross-ref density:       0.05 (paper-level breadth)
        """
        components: list[tuple[float, float]] = [
            (self.information_density.score(), 0.10),
            (self.factual_specificity.score(), 0.15),
        ]
        if self.coverage_ratio is not None:
            components.append((min(self.coverage_ratio, 1.0), 0.15))
        if self.semantic_efficiency is not None:
            components.append((self.semantic_efficiency.score(), 0.10))
        if self.cross_reference_density is not None:
            components.append((self.cross_reference_density.score(), 0.05))
        if self.thematic_centroid is not None:
            components.append((self.thematic_centroid.score(), 0.15))
        if self.topic_coverage is not None:
            components.append((self.topic_coverage.score(), 0.15))
        if self.reconstruction_fidelity is not None:
            components.append((self.reconstruction_fidelity.score(), 0.15))
        if self.semantic_span is not None:
            components.append((self.semantic_span.score(), 0.10))
        if self.argumentative_coherence is not None:
            components.append((self.argumentative_coherence.score(), 0.15))

        total_weight = sum(w for _, w in components)
        if total_weight == 0:
            return 0.0
        return sum(s * w for s, w in components) / total_weight

    def summary(self) -> str:
        """Human-readable multi-section report."""
        lines = [
            "=" * 60,
            "COMPREHENSIVE REVIEW QUALITY REPORT",
            "=" * 60,
        ]

        # New metrics first (most informative)
        if self.thematic_centroid is not None:
            lines += ["", self.thematic_centroid.interpretation()]
        if self.topic_coverage is not None:
            lines += ["", self.topic_coverage.interpretation()]
        if self.reconstruction_fidelity is not None:
            lines += ["", self.reconstruction_fidelity.interpretation()]
        if self.semantic_span is not None:
            lines += ["", self.semantic_span.interpretation()]
        if self.argumentative_coherence is not None:
            lines += ["", self.argumentative_coherence.interpretation()]

        lines += ["", self.factual_specificity.interpretation()]
        lines += ["", self.information_density.interpretation()]

        if self.semantic_efficiency is not None:
            lines += ["", self.semantic_efficiency.interpretation()]

        if self.cross_reference_density is not None:
            lines += ["", self.cross_reference_density.interpretation()]

        if self.corpus_error:
            lines += ["", f"[Corpus unavailable: {self.corpus_error}]"]

        lines += [
            "",
            "=" * 60,
            f"COMPOSITE QUALITY SCORE: {self.composite_score():.3f} / 1.000",
            "=" * 60,
        ]
        return "\n".join(lines)


def comprehensive_quality_report(
    review_text: str,
    coverage_threshold: float = 0.5,
    corpus_text: Optional[str] = None,
) -> QualityReport:
    """Run all quality metrics on a review and return a structured report.

    Metrics 1-2 are always computed (no corpus needed).
    Metrics 3-4 require a live corpus (SQLite + ChromaDB); if unavailable
    they are set to None in the report rather than crashing.

    Args:
        review_text: The full review text (markdown or plain text).
        coverage_threshold: Cosine distance threshold for coverage/cross-ref metrics.
        corpus_text: Optional pre-loaded corpus text for information density.
            If None, loads from the corpus database automatically.

    Returns:
        QualityReport with all available metrics filled in.
    """
    # -- Corpus-independent metrics ------------------------------------------
    if corpus_text is None:
        corpus_text = _load_corpus_text() or review_text  # fallback: use review itself

    info_density = compute_information_density(review_text, corpus_text)
    factual = compute_factual_specificity(review_text)

    # -- Corpus-dependent metrics --------------------------------------------
    coverage_ratio: Optional[float] = None
    efficiency: Optional[SemanticEfficiencyResult] = None
    xref: Optional[CrossReferenceDensityResult] = None
    corpus_error: Optional[str] = None

    try:
        from scholarforge.evaluate.coverage import compute_coverage

        cov_result = compute_coverage(review_text, threshold=coverage_threshold)
        coverage_ratio = cov_result.coverage_ratio
    except Exception as exc:  # noqa: BLE001
        corpus_error = str(exc)

    if coverage_ratio is not None:
        efficiency = compute_semantic_efficiency(
            review_text,
            coverage_ratio=coverage_ratio,
            threshold=coverage_threshold,
        )

    # Tighter threshold for cross-ref density (0.3 cosine distance) so it discriminates
    xref = compute_cross_reference_density(review_text, threshold=0.3)

    # New metrics
    centroid = compute_thematic_centroid(review_text)
    topics = compute_topic_coverage(review_text)
    recon = compute_reconstruction_fidelity(review_text, corpus_text)
    span = compute_semantic_span(review_text)
    coherence = compute_argumentative_coherence(review_text)

    return QualityReport(
        information_density=info_density,
        factual_specificity=factual,
        coverage_ratio=coverage_ratio,
        semantic_efficiency=efficiency,
        cross_reference_density=xref,
        thematic_centroid=centroid,
        topic_coverage=topics,
        reconstruction_fidelity=recon,
        semantic_span=span,
        argumentative_coherence=coherence,
        corpus_error=corpus_error,
    )
