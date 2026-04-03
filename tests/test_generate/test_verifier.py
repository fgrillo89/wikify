"""Tests for generate/verifier.py — deterministic plan verification."""

from __future__ import annotations

from wikify.generate.verifier import (
    PaperVerificationResult,
    _extract_key_terms,
    _extract_numbered_refs,
    _extract_ref_markers,
    _extract_section_headings,
    _extract_sentences,
    _split_sections,
    _word_count,
    verify_paper,
    verify_section_against_plan,
)
from wikify.store.models import PaperPlan, SectionPlan

# ── Helper function tests ────────────────────────────────────────────────────


class TestWordCount:
    def test_simple(self):
        assert _word_count("hello world foo") == 3

    def test_empty(self):
        assert _word_count("") == 1 or _word_count("") == 0
        # "".split() returns [], len([]) == 0
        assert _word_count("") == 0

    def test_multiline(self):
        assert _word_count("one two\nthree four") == 4


class TestExtractRefMarkers:
    def test_finds_markers(self):
        text = "As shown by [REF:Smith 2021 - ALD paper] and [REF:Kim 2020 - Memristors]."
        refs = _extract_ref_markers(text)
        assert len(refs) == 2
        assert "Smith 2021 - ALD paper" in refs
        assert "Kim 2020 - Memristors" in refs

    def test_no_markers(self):
        assert _extract_ref_markers("No citations here.") == []


class TestExtractNumberedRefs:
    def test_finds_numbered(self):
        refs = _extract_numbered_refs("See [1], [2,3], and [4-6].")
        assert len(refs) == 3

    def test_no_refs(self):
        assert _extract_numbered_refs("Nothing here.") == []


class TestExtractKeyTerms:
    def test_removes_stopwords(self):
        text = "the atomic layer deposition of atomic layer films"
        terms = _extract_key_terms(text, min_count=1)
        assert "the" not in terms
        assert "atomic" in terms
        assert "layer" in terms

    def test_min_count_filter(self):
        text = "deposition atomic atomic layer layer layer"
        terms = _extract_key_terms(text, min_count=2)
        assert "atomic" in terms
        assert "layer" in terms
        assert "deposition" not in terms


class TestExtractSectionHeadings:
    def test_finds_headings(self):
        md = "# Title\n\nSome text\n\n## Introduction\n\nBody\n\n## Methods\n\nMore"
        headings = _extract_section_headings(md)
        assert "Title" in headings
        assert "Introduction" in headings
        assert "Methods" in headings

    def test_no_headings(self):
        assert _extract_section_headings("Just plain text.") == []


class TestExtractSentences:
    def test_splits_sentences(self):
        text = (
            "This is a longer first sentence for testing purposes. "
            "This is a longer second sentence that should also pass. "
            "And a third sentence of reasonable length."
        )
        sentences = _extract_sentences(text)
        assert len(sentences) >= 2

    def test_filters_short(self):
        text = "Hi. Ok. This is a much longer sentence that should pass the filter."
        sentences = _extract_sentences(text)
        assert all(len(s) > 20 for s in sentences)


class TestSplitSections:
    def test_splits_correctly(self):
        md = "## Intro\n\nIntro text here.\n\n## Methods\n\nMethods text here."
        sections = _split_sections(md)
        assert "Intro" in sections
        assert "Methods" in sections
        assert "Intro text here." in sections["Intro"]


# ── Section verification tests ───────────────────────────────────────────────


class TestVerifySectionAgainstPlan:
    def _make_plan(self, **kwargs) -> SectionPlan:
        defaults = {
            "heading": "Methods",
            "level": 2,
            "description": "Describe atomic layer deposition techniques and parameters",
            "target_tokens": 200,
            "source_papers": ["Smith 2021 - ALD review", "Kim 2020 - Memristors"],
        }
        defaults.update(kwargs)
        return SectionPlan(**defaults)

    def test_passing_section(self):
        plan = self._make_plan()
        # ~200 words, cites both papers, mentions key terms
        text = (
            "Atomic layer deposition (ALD) techniques have been widely studied. "
            + " ".join(["This describes deposition parameters and methods."] * 20)
            + " As shown by [REF:Smith 2021 - ALD review], the techniques are effective. "
            + "Furthermore, [REF:Kim 2020 - Memristors] demonstrated similar parameters."
        )
        issues = verify_section_against_plan(text, plan)
        assert issues == []

    def test_too_short(self):
        plan = self._make_plan(target_tokens=500)
        text = "This is a very short section. [REF:Smith 2021 - ALD review]"
        issues = verify_section_against_plan(text, plan)
        assert any("Word count too low" in i for i in issues)

    def test_too_long(self):
        plan = self._make_plan(target_tokens=20)
        text = " ".join(["word"] * 100) + " [REF:Smith 2021 - ALD review]"
        issues = verify_section_against_plan(text, plan)
        assert any("Word count too high" in i for i in issues)

    def test_missing_citations(self):
        plan = self._make_plan()
        # No [REF:...] markers at all
        text = " ".join(["Atomic layer deposition techniques and parameters."] * 30)
        issues = verify_section_against_plan(text, plan)
        assert any("Source paper coverage" in i for i in issues)

    def test_description_coverage_low(self):
        plan = self._make_plan(description="quantum computing algorithms for error correction")
        # Text about something completely different
        text = " ".join(["The weather forecast predicts rain tomorrow."] * 30)
        text += " [REF:Smith 2021 - ALD review] [REF:Kim 2020 - Memristors]"
        issues = verify_section_against_plan(text, plan)
        assert any("Description coverage" in i for i in issues)

    def test_no_source_papers_skips_citation_check(self):
        plan = self._make_plan(source_papers=[])
        text = " ".join(["Some content here."] * 30)
        issues = verify_section_against_plan(text, plan)
        # Should not complain about citations
        assert not any("Source paper coverage" in i for i in issues)

    def test_zero_target_tokens_skips_word_check(self):
        plan = self._make_plan(target_tokens=0)
        text = "Short."
        issues = verify_section_against_plan(text, plan)
        assert not any("Word count" in i for i in issues)


# ── Full paper verification tests ────────────────────────────────────────────


class TestVerifyPaper:
    def _make_plan(self) -> PaperPlan:
        return PaperPlan(
            title="Test Review",
            paper_type="lit_review",
            target_length=300,
            sections=[
                SectionPlan(
                    heading="Introduction",
                    level=2,
                    description="Intro",
                    target_tokens=100,
                ),
                SectionPlan(
                    heading="Methods",
                    level=2,
                    description="Methods",
                    target_tokens=100,
                ),
                SectionPlan(
                    heading="Conclusion",
                    level=2,
                    description="Conclusion",
                    target_tokens=100,
                ),
            ],
        )

    def test_passing_paper(self):
        plan = self._make_plan()
        md = (
            "# Test Review\n\n"
            "## Introduction\n\n" + " ".join(["Introduction content."] * 50) + "\n\n"
            "## Methods\n\n" + " ".join(["Methods content."] * 50) + "\n\n"
            "## Conclusion\n\n" + " ".join(["Conclusion content."] * 50)
        )
        result = verify_paper(md, plan)
        assert isinstance(result, PaperVerificationResult)
        assert result.sections_found == 3
        assert result.sections_planned == 3
        assert result.unresolved_refs == []

    def test_missing_section(self):
        plan = self._make_plan()
        md = (
            "# Test Review\n\n"
            "## Introduction\n\n" + " ".join(["Content."] * 50) + "\n\n"
            "## Conclusion\n\n" + " ".join(["Content."] * 50)
        )
        result = verify_paper(md, plan)
        assert result.sections_found == 2
        assert any("Missing planned sections" in i for i in result.issues)

    def test_unresolved_refs(self):
        plan = self._make_plan()
        md = (
            "# Test Review\n\n"
            "## Introduction\n\n" + " ".join(["Content."] * 50) + " [?:unknown] \n\n"
            "## Methods\n\n" + " ".join(["Content."] * 50) + "\n\n"
            "## Conclusion\n\n" + " ".join(["Content."] * 50)
        )
        result = verify_paper(md, plan)
        assert len(result.unresolved_refs) == 1
        assert any("unresolved reference" in i for i in result.issues)

    def test_paper_too_short(self):
        plan = self._make_plan()
        plan.target_length = 10000  # Huge target
        md = (
            "# Test Review\n\n"
            "## Introduction\n\nShort.\n\n"
            "## Methods\n\nShort.\n\n"
            "## Conclusion\n\nShort."
        )
        result = verify_paper(md, plan)
        assert any("too short" in i for i in result.issues)

    def test_duplicate_content_detected(self):
        plan = self._make_plan()
        shared = (
            "This is a shared sentence that appears in both sections and is long enough. "
            "Another shared sentence here in both places for testing duplicate detection. "
            "Third shared sentence to cross the threshold of three identical sentences. "
            "Fourth shared sentence to make sure we clearly exceed three matches here."
        )
        md = (
            "# Test Review\n\n"
            f"## Introduction\n\n{shared}\n\n"
            f"## Methods\n\n{shared}\n\n"
            "## Conclusion\n\nUnique content here."
        )
        result = verify_paper(md, plan)
        assert len(result.duplicate_content) > 0

    def test_result_model_fields(self):
        plan = self._make_plan()
        md = "# Test Review\n\n## Introduction\n\nHello world."
        result = verify_paper(md, plan)
        assert isinstance(result.passed, bool)
        assert isinstance(result.total_words, int)
        assert isinstance(result.issues, list)
        assert isinstance(result.unresolved_refs, list)
        assert isinstance(result.duplicate_content, list)
