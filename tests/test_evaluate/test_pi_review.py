"""Tests for pi_review parsing and structured result types."""

from __future__ import annotations

import pytest

from scholarforge.evaluate.pi_review import (
    PIReviewResult,
    parse_pi_review,
    parse_pi_scores,
)

_SAMPLE_REPORT = """\
## PI Review

| Criterion | Score | Comment |
|-----------|-------|---------|
| Scientific accuracy | 8/10 | Claims are well-supported. |
| Argument progression | 7/10 | Each section builds coherently. |
| Synthesis quality | 9/10 | Strong cross-paper reasoning. |
| Gap identification | 8/10 | Gaps are specific and actionable. |
| Citation integration | 7/10 | Citations functional but front-loaded. |
| Prose quality | 6/10 | Some hedging; abstract is overlong. |
| Specificity | 8/10 | Good numeric density. |

**Overall: 7.6/10**

**Verdict:** This review is close to publishable. The primary weakness is abstract length.
Tighten the abstract to one concept per sentence.

**Strongest sentence:** "Analog linearity is primarily an oxygen diffusion problem."

**Weakest section:** Introduction -- too broad, does not end with a clear gap statement.
"""


def test_parse_pi_scores_extracts_all_criteria() -> None:
    scores = parse_pi_scores(_SAMPLE_REPORT)
    assert scores["scientific_accuracy"] == pytest.approx(8.0)
    assert scores["argument_progression"] == pytest.approx(7.0)
    assert scores["synthesis_quality"] == pytest.approx(9.0)
    assert scores["gap_identification"] == pytest.approx(8.0)
    assert scores["citation_integration"] == pytest.approx(7.0)
    assert scores["prose_quality"] == pytest.approx(6.0)
    assert scores["specificity"] == pytest.approx(8.0)
    assert scores["overall"] == pytest.approx(7.6)


def test_parse_pi_scores_empty_input() -> None:
    assert parse_pi_scores("") == {}


def test_parse_pi_review_returns_result() -> None:
    result = parse_pi_review(_SAMPLE_REPORT)
    assert isinstance(result, PIReviewResult)
    assert result.overall_score == pytest.approx(7.6)
    assert "close to publishable" in result.verdict
    assert "oxygen diffusion" in result.strongest_sentence
    assert "Introduction" in result.weakest_section


def test_parse_pi_review_report_preserved() -> None:
    result = parse_pi_review(_SAMPLE_REPORT)
    assert result.report == _SAMPLE_REPORT


def test_parse_pi_review_missing_fields() -> None:
    minimal = "## PI Review\n\n**Overall: 5/10**\n"
    result = parse_pi_review(minimal)
    assert result.overall_score == pytest.approx(5.0)
    assert result.verdict == ""
    assert result.strongest_sentence == ""
    assert result.weakest_section == ""


def test_parse_pi_review_partial_criteria() -> None:
    partial = """\
## PI Review

| Criterion | Score | Comment |
|-----------|-------|---------|
| Scientific accuracy | 9/10 | Good. |

**Overall: 9/10**

**Verdict:** Excellent work.

**Strongest sentence:** Key finding here.

**Weakest section:** Conclusion lacks specificity.
"""
    result = parse_pi_review(partial)
    assert result.scores["scientific_accuracy"] == pytest.approx(9.0)
    assert result.overall_score == pytest.approx(9.0)
    assert result.weakest_section == "Conclusion lacks specificity."
