"""Tests for KG tool functions used by the guided-mode orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock

from wikify.distill.kg_tools import (
    KG_TOOL_NAMES,
    TOOL_SCHEMAS,
    get_budget,
    get_coverage,
    get_pages,
)


def test_kg_tool_names_match_schemas():
    assert KG_TOOL_NAMES == set(TOOL_SCHEMAS)


def test_get_coverage_returns_subset():
    snapshot = {
        "content_stats": {"n_chunks": 100, "n_seen": 20},
        "doc_coverage": {"d1": 5},
        "residual_histogram": {"0.0-0.2": 10},
        "top_gap_chunks": [{"chunk_id": f"c{i}"} for i in range(15)],
    }
    result = get_coverage(snapshot)
    assert result["content_stats"]["n_chunks"] == 100
    # top_gap_chunks capped at 10
    assert len(result["top_gap_chunks"]) == 10


def test_get_budget_returns_correct_values():
    meter = MagicMock()
    meter.spent_haiku_eq = 15_000.0
    result = get_budget(meter, budget_target=50_000.0)
    assert result["spent_haiku_eq"] == 15_000.0
    assert result["remaining_haiku_eq"] == 35_000.0
    assert result["budget_target_haiku_eq"] == 50_000.0


def test_get_pages_returns_summaries():
    page = MagicMock()
    page.id = "Memristors"
    page.title = "Memristors"
    page.kind = "article"
    page.evidence = [1, 2, 3]
    page.body_markdown = "Some content"
    result = get_pages([page])
    assert len(result) == 1
    assert result[0]["id"] == "Memristors"
    assert result[0]["n_evidence"] == 3
    assert result[0]["has_body"] is True


def test_get_pages_empty_body():
    page = MagicMock()
    page.id = "Stub"
    page.title = "Stub"
    page.kind = "article"
    page.evidence = []
    page.body_markdown = ""
    result = get_pages([page])
    assert result[0]["has_body"] is False
