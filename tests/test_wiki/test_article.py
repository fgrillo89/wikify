"""Tests for wiki/article.py -- Wikipedia-format article writer (Pass 3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import wikify.wiki.article as mod
from wikify.core.store.models import ConceptRecord, ConceptRelation
from wikify.wiki.mapreduce import SourceExtraction

# ── Helpers / fixtures ────────────────────────────────────────────────────────


def _make_concept(
    cid: str = "ald",
    name: str = "Atomic Layer Deposition",
    importance: float = 0.8,
    concept_type: str = "technique",
) -> ConceptRecord:
    return ConceptRecord(
        id=cid,
        name=name,
        importance=importance,
        concept_type=concept_type,
        domain="materials",
    )


def _make_extraction(
    source_id: str = "paper1",
    extraction: str = "YES: ALD is a thin film technique.",
    is_relevant: bool = True,
) -> SourceExtraction:
    return SourceExtraction(
        source_id=source_id,
        display_name="Smith 2024 - Paper",
        doc_type="paper",
        graph_role="standard",
        pagerank_score=0.0,
        extraction=extraction,
        is_relevant=is_relevant,
    )


def _make_session(relations: list[ConceptRelation]) -> MagicMock:
    """Return a context-manager mock whose exec().all() yields *relations*."""
    exec_result = MagicMock()
    exec_result.all.return_value = relations

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = exec_result
    return session


# ── should_write_full ─────────────────────────────────────────────────────────


def test_should_write_full_true():
    concept = _make_concept(importance=0.5)
    extractions = [
        _make_extraction(source_id="p1", is_relevant=True),
        _make_extraction(source_id="p2", is_relevant=True),
        _make_extraction(source_id="p3", is_relevant=True),
        _make_extraction(source_id="p4", is_relevant=False),
    ]
    assert mod.should_write_full(concept, extractions) is True


def test_should_write_full_false_low_importance():
    concept = _make_concept(importance=0.2)
    extractions = [_make_extraction(source_id=f"p{i}", is_relevant=True) for i in range(5)]
    assert mod.should_write_full(concept, extractions) is False


def test_should_write_full_false_few_extractions():
    concept = _make_concept(importance=0.8)
    extractions = [
        _make_extraction(source_id="p1", is_relevant=True),
        _make_extraction(source_id="p2", is_relevant=True),
    ]
    assert mod.should_write_full(concept, extractions) is False


# ── _build_relationships_table ────────────────────────────────────────────────


def test_build_relationships_table_with_relations():
    neighbor_a = _make_concept(cid="cvd", name="Chemical Vapor Deposition")
    neighbor_b = _make_concept(cid="mld", name="Molecular Layer Deposition")

    rel_a = ConceptRelation(
        source_concept="ald",
        target_concept="cvd",
        relation_type="contrasts_with",
        weight=0.7,
        epoch=1,
    )
    rel_b = ConceptRelation(
        source_concept="mld",
        target_concept="ald",
        relation_type="extends",
        weight=0.5,
        epoch=1,
    )

    mock_session = _make_session([rel_a, rel_b])

    with patch("wikify.wiki.article.get_session", return_value=mock_session):
        result = mod._build_relationships_table("ald", [neighbor_a, neighbor_b])

    assert "| Related Concept | Relation | Notes |" in result
    assert "[[cvd]]" in result
    assert "[[mld]]" in result
    assert "contrasts_with" in result
    assert "extends" in result


def test_build_relationships_table_empty():
    mock_session = _make_session([])

    with patch("wikify.wiki.article.get_session", return_value=mock_session):
        result = mod._build_relationships_table("ald", [])

    assert result == ""


def test_build_relationships_table_neighbor_without_relation():
    """Neighbors not present in DB relations appear with 'related' as the type."""
    neighbor = _make_concept(cid="pvd", name="Physical Vapor Deposition")
    mock_session = _make_session([])  # no DB relations

    with patch("wikify.wiki.article.get_session", return_value=mock_session):
        result = mod._build_relationships_table("ald", [neighbor])

    assert "[[pvd]]" in result
    assert "related" in result


# ── _wikipedia_reduce_prompt ──────────────────────────────────────────────────


def test_wikipedia_reduce_prompt_structure():
    persona = "You are a senior ALD process engineer."
    relationships_table = "| Related | Relation | Notes |"

    system_msg, user_msg = mod._wikipedia_reduce_prompt(
        topic="ALD",
        definition="A deposition technique",
        evidence_block="evidence text",
        persona=persona,
        relationships_table=relationships_table,
    )

    # System message carries the persona
    assert persona in system_msg

    # User message contains all six required section headers
    for section in (
        "## Definition",
        "## Mechanism",
        "## Key Facts",
        "## In This Corpus",
        "## Relationships",
        "## Open Questions",
    ):
        assert section in user_msg, f"Missing section '{section}' in user message"

    # User message embeds the evidence and relationships table
    assert "evidence text" in user_msg
    assert relationships_table in user_msg


# ── write_concept_article ─────────────────────────────────────────────────────


def test_write_concept_article_calls_pipeline():
    concept = _make_concept()
    mock_session = _make_session([])

    with (
        patch("wikify.wiki.article.get_or_create_persona", return_value="persona text") as mock_persona,
        patch("wikify.wiki.article.map_chunks_to_topic", return_value=[_make_extraction()]) as mock_map,
        patch("wikify.wiki.article._build_evidence_block", return_value="evidence block") as mock_evidence,
        patch("wikify.wiki.article.complete", return_value="## Definition\nALD is...") as mock_complete,
        patch("wikify.wiki.article.record_coverage") as mock_coverage,
        patch("wikify.wiki.article.get_session", return_value=mock_session),
    ):
        result = mod.write_concept_article(concept, neighbors=[], domain="materials")

    mock_persona.assert_called_once()
    mock_map.assert_called_once()
    # map_chunks_to_topic must receive the concept name as topic_query
    call_kwargs = mock_map.call_args
    assert concept.name in (call_kwargs.args + tuple(call_kwargs.kwargs.values()))

    mock_complete.assert_called_once()
    mock_coverage.assert_called_once()

    assert result == "## Definition\nALD is..."


# ── upgrade_concept_article ───────────────────────────────────────────────────


def test_upgrade_concept_article_additive(tmp_path):
    concept = _make_concept()
    article_file = tmp_path / "ald.md"
    article_file.write_text("## Definition\nExisting content.", encoding="utf-8")

    extractions = [_make_extraction(is_relevant=True)]

    with (
        patch("wikify.wiki.article.get_or_create_persona", return_value="persona text"),
        patch("wikify.wiki.article.detect_contradiction", return_value=False),
        patch("wikify.wiki.article.additive_update", return_value="updated article body") as mock_additive,
        patch("wikify.wiki.article.revisionary_update") as mock_revisionary,
    ):
        result = mod.upgrade_concept_article(concept, article_file, extractions, "materials")

    mock_additive.assert_called_once()
    mock_revisionary.assert_not_called()
    assert result == "updated article body"


def test_upgrade_concept_article_revisionary(tmp_path):
    concept = _make_concept()
    article_file = tmp_path / "ald.md"
    article_file.write_text("## Definition\nExisting content.", encoding="utf-8")

    extractions = [_make_extraction(is_relevant=True)]

    with (
        patch("wikify.wiki.article.get_or_create_persona", return_value="persona text"),
        patch("wikify.wiki.article.detect_contradiction", return_value=True),
        patch("wikify.wiki.article.additive_update") as mock_additive,
        patch("wikify.wiki.article.revisionary_update", return_value="revised body") as mock_revisionary,
    ):
        result = mod.upgrade_concept_article(concept, article_file, extractions, "materials")

    mock_revisionary.assert_called_once()
    mock_additive.assert_not_called()
    assert result == "revised body"
