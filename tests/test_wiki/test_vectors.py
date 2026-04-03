"""Tests for wiki/vectors.py -- Structured concept vector building."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wikify.store.models import ConceptRecord
from wikify.wiki import vectors as mod


def _make_concept(
    cid="ald",
    name="Atomic Layer Deposition",
    concept_type="technique",
    definition="Sequential self-limiting deposition",
):
    return ConceptRecord(
        id=cid,
        name=name,
        concept_type=concept_type,
        definition=definition,
    )


def _mock_session(relations=None, params=None):
    """Return a session mock that returns relations then params."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    results = []
    for data in [relations or [], params or []]:
        r = MagicMock()
        r.all.return_value = data
        results.append(r)

    session.exec.side_effect = results
    return session


def test_basic_structured_text():
    """Includes name, type, and definition."""
    concept = _make_concept()
    session = _mock_session()

    with patch("wikify.wiki.vectors.get_session", return_value=session):
        result = mod.build_structured_text(concept)

    assert "Atomic Layer Deposition" in result
    assert "type:technique" in result
    assert "def:Sequential self-limiting" in result


def test_structured_text_with_relations():
    """Includes relation types and targets."""
    concept = _make_concept()

    rel = MagicMock()
    rel.source_concept = "ald"
    rel.target_concept = "rram"
    rel.relation_type = "USED-IN"

    session = _mock_session(relations=[rel])

    with patch("wikify.wiki.vectors.get_session", return_value=session):
        result = mod.build_structured_text(concept)

    assert "used_in:rram" in result


def test_structured_text_with_params():
    """Includes extracted parameters."""
    concept = _make_concept()

    param = MagicMock()
    param.concept_id = "ald"
    param.parameter_name = "growth_rate"
    param.value = "1.0"
    param.unit = "A/cycle"

    session = _mock_session(params=[param])

    with patch("wikify.wiki.vectors.get_session", return_value=session):
        result = mod.build_structured_text(concept)

    assert "params:growth_rate=1.0_A/cycle" in result


def test_structured_text_no_type():
    """Works without concept_type."""
    concept = _make_concept(concept_type="")
    session = _mock_session()

    with patch("wikify.wiki.vectors.get_session", return_value=session):
        result = mod.build_structured_text(concept)

    assert "type:" not in result
    assert "Atomic Layer Deposition" in result


def test_build_structured_texts_batch():
    """Batch function returns one string per concept."""
    concepts = [_make_concept(cid="a", name="A"), _make_concept(cid="b", name="B")]
    session = _mock_session()

    with patch("wikify.wiki.vectors.get_session", return_value=session):
        results = mod.build_structured_texts(concepts)

    assert len(results) == 2
    assert "A" in results[0]
    assert "B" in results[1]
