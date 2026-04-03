from __future__ import annotations

import pytest

from scholarforge.evaluate.quality import (
    FactualSpecificityResult,
    ProseQualityResult,
    QualityReport,
)


def _high_factual_specificity() -> FactualSpecificityResult:
    return FactualSpecificityResult(
        numeric_with_units=40,
        chemical_formulas=0,
        author_citations=0,
        comparative_sentences=0,
        technical_acronyms=0,
        word_count=1,
    )


def _low_prose_quality() -> ProseQualityResult:
    return ProseQualityResult(
        citation_clustering_ratio=1.0,
        multi_cite_fraction=0.0,
        deep_synthesis_fraction=0.0,
        surface_comparison_fraction=0.0,
        single_paper_fraction=0.5,
        opening_entropy=0.0,
        author_et_al_fraction=0.0,
    )


def _high_prose_quality() -> ProseQualityResult:
    return ProseQualityResult(
        citation_clustering_ratio=2.0,
        multi_cite_fraction=1.0,
        deep_synthesis_fraction=0.1,
        surface_comparison_fraction=0.0,
        single_paper_fraction=0.2,
        opening_entropy=1.0,
        author_et_al_fraction=0.0,
    )


def test_composite_score_includes_prose_quality_weight() -> None:
    factual = _high_factual_specificity()

    low_prose_report = QualityReport(
        prose_quality=_low_prose_quality(),
        factual_specificity=factual,
    )
    high_prose_report = QualityReport(
        prose_quality=_high_prose_quality(),
        factual_specificity=factual,
    )

    assert low_prose_report.composite_score() == pytest.approx(0.375, rel=1e-6)
    assert high_prose_report.composite_score() == pytest.approx(1.0, rel=1e-6)
    assert high_prose_report.composite_score() > low_prose_report.composite_score()


def test_quality_report_summary_includes_prose_quality() -> None:
    report = QualityReport(
        prose_quality=_high_prose_quality(),
        factual_specificity=_high_factual_specificity(),
    )

    summary = report.summary()

    assert "Prose Quality:" in summary
    assert "COMPOSITE QUALITY SCORE" in summary


def test_comprehensive_quality_report_populates_prose_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scholarforge.evaluate import quality as quality_module

    prose = _high_prose_quality()
    factual = _high_factual_specificity()

    monkeypatch.setattr(quality_module, "compute_prose_quality", lambda review_text: prose)
    monkeypatch.setattr(quality_module, "compute_factual_specificity", lambda review_text: factual)
    monkeypatch.setattr(quality_module, "compute_topic_coverage", lambda review_text: None)
    monkeypatch.setattr(quality_module, "_build_embedding_context", lambda review_text: None)

    report = quality_module.comprehensive_quality_report("A short review draft.")

    assert report.prose_quality is prose
    assert report.factual_specificity is factual
    assert report.corpus_error == "Corpus unavailable or empty"
    assert "Prose Quality:" in report.summary()
