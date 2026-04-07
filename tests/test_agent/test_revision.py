"""Tests for the targeted revision module."""

from __future__ import annotations

from unittest.mock import patch

from wikify.papers.agent.revision import _find_section_bounds, revise_weakest_section
from wikify.papers.evaluate.pi_review import PIReviewResult

_SAMPLE_REVIEW = """\
## Abstract

This review examines ALD memristors.

## Introduction

The introduction is too broad.

## Switching Mechanisms

Switching mechanisms involve various processes.

## Research Agenda

Future work should focus on several areas.

## Conclusion

The field is advancing rapidly.

## References

[1] Author 2021 - Title
"""


def test_find_section_bounds_exact_match() -> None:
    start, end = _find_section_bounds(_SAMPLE_REVIEW, "Introduction")
    section = _SAMPLE_REVIEW[start:end]
    assert section.startswith("## Introduction")
    assert "too broad" in section
    assert "## Switching Mechanisms" not in section


def test_find_section_bounds_case_insensitive() -> None:
    result = _find_section_bounds(_SAMPLE_REVIEW, "conclusion")
    assert result is not None
    start, end = result
    assert _SAMPLE_REVIEW[start:].startswith("## Conclusion")


def test_find_section_bounds_not_found() -> None:
    result = _find_section_bounds(_SAMPLE_REVIEW, "Nonexistent Section")
    assert result is None


def test_find_section_bounds_last_section_ends_before_references() -> None:
    result = _find_section_bounds(_SAMPLE_REVIEW, "Conclusion")
    assert result is not None
    start, end = result
    section = _SAMPLE_REVIEW[start:end]
    assert "## References" not in section


def _make_pi_result(weakest: str) -> PIReviewResult:
    return PIReviewResult(
        report="",
        scores={},
        overall_score=7.5,
        verdict="Needs work.",
        weakest_section=weakest,
    )


def test_revise_weakest_section_replaces_section() -> None:
    pi_result = _make_pi_result("Introduction -- too broad")

    revised_section = "## Introduction\n\nA tighter introduction with clear gap statement.\n\n"

    with (
        patch(
            "wikify.papers.agent.revision._fetch_section_evidence",
            return_value="evidence text",
        ),
        patch(
            "wikify.llm.client.complete",
            return_value=revised_section,
        ),
    ):
        revised = revise_weakest_section(_SAMPLE_REVIEW, pi_result, topic="ALD memristors")

    assert "tighter introduction" in revised
    assert "too broad" not in revised
    # Other sections untouched
    assert "## Abstract" in revised
    assert "## Switching Mechanisms" in revised


def test_revise_weakest_section_no_weakest_returns_original() -> None:
    pi_result = _make_pi_result("")

    result = revise_weakest_section(_SAMPLE_REVIEW, pi_result)
    assert result == _SAMPLE_REVIEW


def test_revise_weakest_section_missing_section_returns_original() -> None:
    pi_result = _make_pi_result("Methodology -- not found")

    with patch("wikify.papers.agent.revision._fetch_section_evidence", return_value=""):
        result = revise_weakest_section(_SAMPLE_REVIEW, pi_result)

    assert result == _SAMPLE_REVIEW
