"""Tests for store/gc.py -- Database garbage collection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wikify.core.store.models import (
    ConceptEvidence,
    ConceptRecord,
    ConceptRelation,
    Paper,
    ParameterExtraction,
)


def _mock_session(concepts=None, evidence=None, params=None, relations=None, papers=None):
    """Build a mock session that returns specified data for select queries."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    all_data = {
        ConceptRecord: concepts or [],
        ConceptEvidence: evidence or [],
        ParameterExtraction: params or [],
        ConceptRelation: relations or [],
        Paper: papers or [],
    }

    def fake_exec(stmt):
        # Determine which model is being queried from the statement
        result = MagicMock()
        for model_cls, data in all_data.items():
            if hasattr(stmt, "froms") or True:  # always return all
                pass
        # Default: return first matching type based on call order
        result.all.return_value = all_data.get(ConceptRecord, [])
        return result

    session.exec.return_value = MagicMock(all=MagicMock(return_value=[]))
    return session


def test_integrity_check_clean():
    """Clean DB reports zero orphans."""
    from wikify.core.store.gc import integrity_check

    c1 = ConceptRecord(id="ald", name="ALD")
    p1 = MagicMock()
    p1.id = "paper1"

    e1 = ConceptEvidence(concept_id="ald", paper_id="paper1")

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    results = [
        MagicMock(all=MagicMock(return_value=[c1])),  # concepts
        MagicMock(all=MagicMock(return_value=[e1])),  # evidence
        MagicMock(all=MagicMock(return_value=[])),  # params
        MagicMock(all=MagicMock(return_value=[])),  # relations
        MagicMock(all=MagicMock(return_value=[p1])),  # papers
    ]
    session.exec.side_effect = results

    with patch("wikify.core.store.gc.get_session", return_value=session):
        report = integrity_check()

    assert report["orphan_evidence"] == 0
    assert report["orphan_params"] == 0
    assert report["dangling_relations"] == 0


def test_integrity_check_detects_orphans():
    """Detects evidence pointing to nonexistent concept."""
    from wikify.core.store.gc import integrity_check

    c1 = ConceptRecord(id="ald", name="ALD")
    e_orphan = ConceptEvidence(concept_id="nonexistent", paper_id="p1")
    p1 = MagicMock()
    p1.id = "p1"

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    results = [
        MagicMock(all=MagicMock(return_value=[c1])),
        MagicMock(all=MagicMock(return_value=[e_orphan])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[p1])),
    ]
    session.exec.side_effect = results

    with patch("wikify.core.store.gc.get_session", return_value=session):
        report = integrity_check()

    assert report["orphan_evidence"] == 1


def test_redirect_merged():
    """Redirects evidence from merged concept to primary."""
    from wikify.core.store.gc import redirect_merged

    primary = ConceptRecord(id="ald", name="ALD", article_status="full")
    merged = ConceptRecord(
        id="atomic_layer_dep",
        name="Atomic Layer Dep",
        article_status="merged:ald",
    )

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    # First call: get all concepts
    concepts_result = MagicMock(all=MagicMock(return_value=[primary, merged]))
    # Subsequent calls: get evidence/params/relations for merged id
    empty = MagicMock(all=MagicMock(return_value=[]))
    evidence_for_merged = MagicMock(
        all=MagicMock(return_value=[ConceptEvidence(concept_id="atomic_layer_dep")])
    )

    session.exec.side_effect = [
        concepts_result,
        evidence_for_merged,  # evidence for merged
        empty,  # params for merged
        empty,  # relations source
        empty,  # relations target
    ]

    with patch("wikify.core.store.gc.get_session", return_value=session):
        count = redirect_merged()

    assert count >= 1
