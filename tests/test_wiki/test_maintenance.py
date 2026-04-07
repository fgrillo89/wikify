"""Tests for wiki/maintenance.py -- three-tier maintenance."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np

from wikify.wiki.maintenance import (
    StructuralReport,
    _extract_established_section,
    _strip_frontmatter,
    additive_update,
    detect_contradiction,
    revisionary_update,
    structural_audit,
)

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_extraction(
    source_id="abc123",
    display_name="Smith 2024 - Test",
    doc_type="paper",
    graph_role="standard",
    pagerank_score=0.0,
    extraction="YES: Key finding here.",
    is_relevant=True,
):
    from wikify.wiki.mapreduce import SourceExtraction

    return SourceExtraction(
        source_id=source_id,
        display_name=display_name,
        doc_type=doc_type,
        graph_role=graph_role,
        pagerank_score=pagerank_score,
        extraction=extraction,
        is_relevant=is_relevant,
    )


def _make_article(
    article_id="slug_one",
    title="Article One",
    source_ids=None,
    domain="test_domain",
    file_path="wiki/concepts/slug_one.md",
    needs_update=True,
):
    art = MagicMock()
    art.id = article_id
    art.title = title
    art.source_ids = json.dumps(source_ids or ["src_a", "src_b"])
    art.domain = domain
    art.file_path = file_path
    art.needs_update = needs_update
    art.status = "draft"
    art.model = "test"
    art.topic_keys = "[]"
    return art


SAMPLE_BODY = """\
## What Is Known

Hafnium oxide grows at 0.1 nm/cycle. [REF:Smith 2021]
The temperature window is 200-300 C. [REF:Jones 2022]

## Where the Field Disagrees

Some groups report 0.12 nm/cycle. [REF:Lee 2023]

## Unresolved Questions

Whether precursor pulsing order matters.

## Source Pointers

Smith 2021 - Results: GPC data table.
"""


# ── _extract_established_section ─────────────────────────────────────────────


class TestExtractEstablishedSection:
    def test_extracts_what_is_known(self):
        text = "## What Is Known\n\nFact A. Fact B.\n\n## Next Section\n\nOther."
        result = _extract_established_section(text)
        assert "Fact A" in result
        assert "Next Section" not in result

    def test_extracts_practitioner_consensus(self):
        text = "## Practitioner Consensus\n\nPractice A.\n\n## Ongoing Debates\n\nDebate."
        result = _extract_established_section(text)
        assert "Practice A" in result
        assert "Debate" not in result

    def test_extracts_established(self):
        text = "## Established\n\nClaim X.\n\n## Points of Tension\n\nTension."
        result = _extract_established_section(text)
        assert "Claim X" in result

    def test_fallback_to_first_500_chars(self):
        text = "No heading here.\n\nJust plain text that does not match any pattern."
        result = _extract_established_section(text)
        assert result  # should return something
        assert len(result) <= 500


# ── _strip_frontmatter ────────────────────────────────────────────────────────


class TestStripFrontmatter:
    def test_strips_yaml_frontmatter(self):
        text = "---\ntitle: Test\n---\nBody text here."
        assert _strip_frontmatter(text) == "Body text here."

    def test_no_frontmatter_unchanged(self):
        text = "Plain body without frontmatter."
        assert _strip_frontmatter(text) == text


# ── detect_contradiction ──────────────────────────────────────────────────────


def _mock_store(encode_return):
    """Create a mock _store with model.encode returning encode_return."""
    mock_model = MagicMock()
    mock_model.encode.return_value = encode_return
    mock_store = MagicMock()
    mock_store.model = mock_model
    return mock_store


class TestDetectContradiction:
    def test_returns_false_for_short_extraction(self):
        """Short extractions (<=50 chars) never trigger contradiction."""
        # No encode call needed -- short extraction returns immediately
        with patch("wikify.wiki.maintenance._store", _mock_store([])):
            result = detect_contradiction("some body", "short")
        assert result is False

    def test_returns_true_for_low_similarity(self):
        """Very dissimilar embeddings -> contradiction flagged."""
        e1 = np.array([1.0, 0.0, 0.0])
        e2 = np.array([0.0, 1.0, 0.0])  # orthogonal -> cosine = 0.0

        mock_st = _mock_store([e1, e2])
        with patch("wikify.wiki.maintenance._store", mock_st):
            result = detect_contradiction(SAMPLE_BODY, "A" * 60)

        assert result is True
        mock_st.model.encode.assert_called_once()

    def test_returns_false_for_high_similarity(self):
        """Nearly identical embeddings -> no contradiction."""
        e1 = np.array([1.0, 0.5, 0.3])
        e2 = np.array([0.99, 0.51, 0.31])  # very similar

        mock_st = _mock_store([e1, e2])
        with patch("wikify.wiki.maintenance._store", mock_st):
            result = detect_contradiction(SAMPLE_BODY, "B" * 60)

        assert result is False

    def test_returns_false_for_empty_established_section(self):
        """If body is empty, established section is also empty -> skip."""
        # detect_contradiction returns False immediately when established is empty
        mock_st = _mock_store([])
        with patch("wikify.wiki.maintenance._store", mock_st):
            result = detect_contradiction("", "A" * 60)

        assert result is False
        mock_st.model.encode.assert_not_called()


# ── additive_update ───────────────────────────────────────────────────────────


class TestAdditiveUpdate:
    def test_calls_complete_with_no_restructure_instruction(self, tmp_path):
        art_file = tmp_path / "article.md"
        art_file.write_text("---\ntitle: Test\n---\n" + SAMPLE_BODY, encoding="utf-8")

        extractions = [
            _make_extraction(extraction="YES: New finding about growth rate.", is_relevant=True)
        ]
        persona = "You are a senior ALD researcher."

        with patch("wikify.llm.client.complete") as mock_complete:
            mock_complete.return_value = "Updated body content"
            result = additive_update(art_file, extractions, persona, model=None)

        assert result == "Updated body content"
        call_args = mock_complete.call_args
        # Find system prompt
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
        system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        assert "Do NOT restructure" in system_content
        assert "Do NOT remove existing content" in system_content
        assert persona in system_content

    def test_skips_irrelevant_extractions(self, tmp_path):
        art_file = tmp_path / "article.md"
        art_file.write_text(SAMPLE_BODY, encoding="utf-8")

        irrelevant = _make_extraction(is_relevant=False)
        relevant = _make_extraction(extraction="YES: Good finding.", is_relevant=True)

        with patch("wikify.llm.client.complete") as mock_complete:
            mock_complete.return_value = "Updated"
            additive_update(art_file, [irrelevant, relevant], "persona", model=None)

        # The evidence block should only include relevant one
        call_args = mock_complete.call_args
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Good finding" in user_content


# ── revisionary_update ────────────────────────────────────────────────────────


class TestRevisionaryUpdate:
    def test_calls_complete_with_warning_instruction(self, tmp_path):
        art_file = tmp_path / "article.md"
        art_file.write_text(SAMPLE_BODY, encoding="utf-8")

        extractions = [
            _make_extraction(
                extraction="YES: Contradicting claim about growth rate.", is_relevant=True
            )
        ]
        persona = "You are a senior ALD researcher."

        with patch("wikify.llm.client.complete") as mock_complete:
            mock_complete.return_value = "Revised body with WARNING marker"
            result = revisionary_update(art_file, extractions, persona, model=None)

        assert result == "Revised body with WARNING marker"
        call_args = mock_complete.call_args
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "WARNING" in system_content
        assert "Do NOT resolve the contradiction" in system_content
        assert persona in system_content

    def test_instruction_to_move_to_contested_zone(self, tmp_path):
        art_file = tmp_path / "article.md"
        art_file.write_text(SAMPLE_BODY, encoding="utf-8")

        extractions = [_make_extraction(is_relevant=True)]

        with patch("wikify.llm.client.complete") as mock_complete:
            mock_complete.return_value = "Revised"
            revisionary_update(art_file, extractions, "persona", model=None)

        call_args = mock_complete.call_args
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][0]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "contested" in system_content.lower()


# ── structural_audit ──────────────────────────────────────────────────────────


class TestStructuralAudit:
    def _make_session_mock(
        self,
        articles=None,
        coverage_counts=None,
        covered_source_ids=None,
        papers=None,
    ):
        """Build a mock session that returns the provided data."""
        articles = articles or []
        coverage_counts = coverage_counts or []
        covered_source_ids = covered_source_ids or []
        papers = papers or []

        session_mock = MagicMock()

        def _exec_side_effect(stmt):
            result_mock = MagicMock()
            # Return different things based on which query this is
            result_mock.all.return_value = []
            return result_mock

        session_mock.exec.side_effect = _exec_side_effect
        session_mock.__enter__ = lambda s: session_mock
        session_mock.__exit__ = MagicMock(return_value=False)

        return session_mock

    def test_split_candidates_above_threshold(self, tmp_path):
        """Articles with >15 SourceCoverage rows should be split candidates."""
        article = _make_article("slug_heavy", source_ids=["s1", "s2"])

        with (
            patch("wikify.store.db.get_session") as mock_gs,
            patch("wikify.papers.agent.tools.get_graph_metrics") as mock_gm,
        ):
            session_mock = MagicMock()
            session_mock.__enter__ = lambda s: session_mock
            session_mock.__exit__ = MagicMock(return_value=False)

            # Return articles
            def exec_side(stmt):
                result = MagicMock()
                # For grouped coverage count, return >15 for slug_heavy
                result.all.return_value = [("slug_heavy", 20)]
                return result

            session_mock.exec.side_effect = exec_side
            mock_gs.return_value = session_mock

            mock_gm.return_value = json.dumps(
                {
                    "hub_papers": [],
                    "bridge_papers": [],
                    "frontier_papers": [],
                    "full_ranking": [],
                }
            )

            # We need the article list query to return our article
            # Use a simpler approach: patch the session exec to return articles first
            call_results = [
                MagicMock(all=lambda: [article]),  # WikiArticle query
                MagicMock(all=lambda: [("slug_heavy", 20)]),  # coverage count
                MagicMock(all=lambda: []),  # covered source_ids
                MagicMock(all=lambda: []),  # papers
            ]
            call_index = [0]

            def exec_by_call(stmt):
                idx = call_index[0]
                call_index[0] += 1
                if idx < len(call_results):
                    return call_results[idx]
                return MagicMock(all=lambda: [])

            session_mock.exec.side_effect = exec_by_call
            report = structural_audit(tmp_path, domain="test_domain")

        assert "slug_heavy" in report.split_candidates

    def test_deprecation_candidates_zero_coverage_few_sources(self, tmp_path):
        """Articles with 0 coverage rows and <3 source_ids are deprecation candidates."""
        article = _make_article("slug_empty", source_ids=["s1"])  # only 1 source

        with (
            patch("wikify.store.db.get_session") as mock_gs,
            patch("wikify.papers.agent.tools.get_graph_metrics") as mock_gm,
        ):
            session_mock = MagicMock()
            session_mock.__enter__ = lambda s: session_mock
            session_mock.__exit__ = MagicMock(return_value=False)

            call_results = [
                MagicMock(all=lambda: [article]),  # WikiArticle query
                MagicMock(all=lambda: []),  # coverage count (0 rows)
                MagicMock(all=lambda: []),  # covered source_ids
                MagicMock(all=lambda: []),  # papers
            ]
            call_index = [0]

            def exec_by_call(stmt):
                idx = call_index[0]
                call_index[0] += 1
                if idx < len(call_results):
                    return call_results[idx]
                return MagicMock(all=lambda: [])

            session_mock.exec.side_effect = exec_by_call
            mock_gs.return_value = session_mock
            mock_gm.return_value = json.dumps(
                {
                    "hub_papers": [],
                    "bridge_papers": [],
                    "frontier_papers": [],
                    "full_ranking": [],
                }
            )

            report = structural_audit(tmp_path, domain="test_domain")

        assert "slug_empty" in report.deprecation_candidates

    def test_contradiction_flags_from_file_content(self, tmp_path):
        """Articles with WARNING in body text should appear in contradiction_flags."""
        # Create article file with WARNING marker
        art_dir = tmp_path / "concepts"
        art_dir.mkdir(parents=True)
        art_file = art_dir / "slug_contradiction.md"
        art_file.write_text(
            "## Established\n\nClaim A [REF:X] WARNING\n\nHowever [REF:Y] disagrees.\n",
            encoding="utf-8",
        )

        article = _make_article(
            "slug_contradiction",
            file_path=str(art_file),
        )
        # Make file_path absolute so the audit doesn't prepend "data/"
        article.file_path = str(art_file)

        with (
            patch("wikify.store.db.get_session") as mock_gs,
            patch("wikify.papers.agent.tools.get_graph_metrics") as mock_gm,
        ):
            session_mock = MagicMock()
            session_mock.__enter__ = lambda s: session_mock
            session_mock.__exit__ = MagicMock(return_value=False)

            call_results = [
                MagicMock(all=lambda: [article]),
                MagicMock(all=lambda: []),
                MagicMock(all=lambda: []),
                MagicMock(all=lambda: []),
            ]
            call_index = [0]

            def exec_by_call(stmt):
                idx = call_index[0]
                call_index[0] += 1
                if idx < len(call_results):
                    return call_results[idx]
                return MagicMock(all=lambda: [])

            session_mock.exec.side_effect = exec_by_call
            mock_gs.return_value = session_mock
            mock_gm.return_value = json.dumps(
                {
                    "hub_papers": [],
                    "bridge_papers": [],
                    "frontier_papers": [],
                    "full_ranking": [],
                }
            )

            report = structural_audit(tmp_path, domain="test_domain")

        assert "slug_contradiction" in report.contradiction_flags

    def test_structural_report_is_dataclass(self):
        report = StructuralReport(domain="test")
        assert report.domain == "test"
        assert report.split_candidates == []
        assert report.merge_candidates == []
        assert report.deprecation_candidates == []
        assert report.orphan_sources == []
        assert report.contradiction_flags == []
        assert report.graph_drift == []
