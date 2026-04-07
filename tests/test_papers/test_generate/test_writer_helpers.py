"""Tests for writer.py helper functions — context compaction and sufficiency checks."""

from __future__ import annotations

from wikify.papers.generate.writer import (
    _check_context_sufficiency,
    _compact_prior_sections,
    _extract_section_summary,
)
from wikify.core.retrieve.context import RetrievedContext
from wikify.core.store.models import Paper, PaperPlan, SectionPlan

# ── Context compaction tests ─────────────────────────────────────────────────


class TestCompactPriorSections:
    def test_empty_returns_empty(self):
        assert _compact_prior_sections([]) == ""

    def test_single_section_returns_full(self):
        sections = ["## Introduction\n\nThis is the introduction text."]
        result = _compact_prior_sections(sections)
        assert "Immediately preceding section (full)" in result
        assert "Introduction" in result
        # With only one section, there's no compacted summary
        assert "Paper so far (compacted)" not in result

    def test_two_sections_first_compacted(self):
        sections = [
            "## Introduction\n\nFirst section with some content about ALD. "
            "ALD is important for thin film deposition. [REF:Smith 2021]",
            "## Methods\n\nSecond section about experimental methods.",
        ]
        result = _compact_prior_sections(sections)
        assert "Paper so far (compacted)" in result
        assert "Immediately preceding section (full)" in result
        # The compacted part should reference Introduction
        assert "Introduction" in result
        # The full part should have the Methods section
        assert "Second section about experimental methods." in result

    def test_three_sections_two_compacted(self):
        sections = [
            "## Introduction\n\nIntro content.",
            "## Background\n\nBackground content.",
            "## Methods\n\nMethods content.",
        ]
        result = _compact_prior_sections(sections)
        # First two should be compacted, last one full
        assert "Paper so far (compacted)" in result
        assert "Introduction" in result
        assert "Background" in result
        assert "Methods content." in result


class TestExtractSectionSummary:
    def test_extracts_heading(self):
        text = "## Introduction\n\nSome body text here."
        heading, _, _, _ = _extract_section_summary(text)
        assert heading == "Introduction"

    def test_extracts_first_sentence(self):
        text = "## Methods\n\nFirst sentence of methods. Second sentence follows."
        _, topic, _, _ = _extract_section_summary(text)
        assert "First sentence" in topic

    def test_extracts_citations(self):
        text = "## Results\n\nAs shown [REF:Smith 2021] and [1] data."
        _, _, citations, _ = _extract_section_summary(text)
        assert any("REF:Smith 2021" in c for c in citations)

    def test_extracts_key_terms(self):
        text = "## Methods\n\nDeposition deposition technique technique parameters."
        _, _, _, terms = _extract_section_summary(text)
        assert "deposition" in terms
        assert "technique" in terms

    def test_no_heading(self):
        text = "Just body text without a heading."
        heading, _, _, _ = _extract_section_summary(text)
        assert heading == ""


# ── Context sufficiency check tests ──────────────────────────────────────────


def _make_paper(pid: str = "p1", title: str = "Test Paper", year: int = 2021) -> Paper:
    return Paper(
        id=pid,
        title=title,
        authors='["Smith"]',
        year=year,
        source_path="test.pdf",
    )


class TestCheckContextSufficiency:
    def test_sufficient_context(self):
        plan = PaperPlan(
            title="Test",
            paper_type="lit_review",
            target_length=1000,
            sections=[
                SectionPlan(
                    heading="Intro",
                    description="Introduction",
                    target_tokens=200,
                    source_papers=["Smith 2021 - Test Paper"],
                ),
            ],
        )
        papers = [_make_paper(f"p{i}", f"Paper {i}") for i in range(5)]
        # Add a paper whose display_name matches the source_papers reference
        papers.append(_make_paper("ptest", "Test Paper", 2021))
        context = RetrievedContext(papers=papers, total_tokens=5000)
        warnings = _check_context_sufficiency(plan, context)
        assert warnings == []

    def test_low_token_count(self):
        plan = PaperPlan(title="Test", paper_type="lit_review", sections=[])
        context = RetrievedContext(papers=[_make_paper()], total_tokens=500)
        warnings = _check_context_sufficiency(plan, context)
        assert any("500 tokens" in w for w in warnings)

    def test_too_few_papers(self):
        plan = PaperPlan(title="Test", paper_type="lit_review", sections=[])
        context = RetrievedContext(papers=[_make_paper()], total_tokens=5000)
        warnings = _check_context_sufficiency(plan, context)
        assert any("1 paper" in w for w in warnings)

    def test_section_source_not_in_context(self):
        plan = PaperPlan(
            title="Test",
            paper_type="lit_review",
            sections=[
                SectionPlan(
                    heading="Methods",
                    description="Methods",
                    target_tokens=200,
                    source_papers=["Nonexistent 2099 - Ghost Paper"],
                ),
            ],
        )
        papers = [_make_paper(f"p{i}", f"Paper {i}") for i in range(5)]
        context = RetrievedContext(papers=papers, total_tokens=5000)
        warnings = _check_context_sufficiency(plan, context)
        assert any("not found in context" in w for w in warnings)

    def test_no_warnings_when_all_good(self):
        plan = PaperPlan(
            title="Test",
            paper_type="lit_review",
            sections=[
                SectionPlan(heading="Intro", description="Intro", target_tokens=200),
            ],
        )
        papers = [_make_paper(f"p{i}", f"Paper {i}") for i in range(5)]
        context = RetrievedContext(papers=papers, total_tokens=5000)
        warnings = _check_context_sufficiency(plan, context)
        assert warnings == []
