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

        Each type of evidence is weighted and summed, then normalised.
        Weights reflect how strongly each marker indicates factual density.
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
        # Normalise per 1,000 words and cap at 1.0 at 40 weighted markers/1k words
        return min(raw / max(self.word_count, 1) * 1000 / 40.0, 1.0)

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
    coverage_ratio: Optional[float] = None  # from existing compute_coverage
    semantic_efficiency: Optional[SemanticEfficiencyResult] = None
    cross_reference_density: Optional[CrossReferenceDensityResult] = None

    # Error info for corpus-dependent failures
    corpus_error: Optional[str] = None

    def composite_score(self) -> float:
        """Weighted average of all available sub-scores.

        Weights:
          - Information density: 0.20
          - Factual specificity: 0.25
          - Coverage ratio:      0.25  (if available)
          - Semantic efficiency: 0.15  (if available)
          - Cross-ref density:   0.15  (if available)
        """
        components: list[tuple[float, float]] = [
            (self.information_density.score(), 0.20),
            (self.factual_specificity.score(), 0.25),
        ]
        if self.coverage_ratio is not None:
            components.append((min(self.coverage_ratio, 1.0), 0.25))
        if self.semantic_efficiency is not None:
            components.append((self.semantic_efficiency.score(), 0.15))
        if self.cross_reference_density is not None:
            components.append((self.cross_reference_density.score(), 0.15))

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
            "",
            self.information_density.interpretation(),
            "",
            self.factual_specificity.interpretation(),
        ]

        if self.semantic_efficiency is not None:
            lines += ["", self.semantic_efficiency.interpretation()]
        elif self.coverage_ratio is not None:
            lines += [
                "",
                f"Semantic Coverage (no efficiency breakdown): {self.coverage_ratio:.1%}",
            ]

        if self.cross_reference_density is not None:
            lines += ["", self.cross_reference_density.interpretation()]

        if self.corpus_error:
            lines += ["", f"[Corpus unavailable: {self.corpus_error}]"]

        lines += [
            "",
            "-" * 60,
            f"COMPOSITE QUALITY SCORE: {self.composite_score():.3f} / 1.000",
            "-" * 60,
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

    xref = compute_cross_reference_density(review_text, threshold=coverage_threshold)
    if xref is None and corpus_error is None:
        corpus_error = "cross_reference_density: corpus unavailable"

    return QualityReport(
        information_density=info_density,
        factual_specificity=factual,
        coverage_ratio=coverage_ratio,
        semantic_efficiency=efficiency,
        cross_reference_density=xref,
        corpus_error=corpus_error,
    )
