"""Tests for the canonical concept persistence boundary."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import wikify.wiki.concepts as mod
from wikify.core.store.models import ConceptRecord

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_chunk(
    chunk_id="chunk1",
    paper_id="paper1",
    content="Some text about ALD techniques and HfO2 thin film deposition.",
    chunk_index=0,
):
    c = MagicMock()
    c.id = chunk_id
    c.paper_id = paper_id
    c.content = content
    c.chunk_index = chunk_index
    c.section_type = "body"
    return c


def _make_concept_record(
    cid="atomic_layer_deposition",
    name="Atomic Layer Deposition",
    aliases=None,
    definition="A thin film deposition method.",
    concept_type="technique",
    domain="",
    importance=0.0,
    epoch_discovered=1,
    epoch_last_updated=1,
):
    if aliases is None:
        aliases = ["ALD"]
    return ConceptRecord(
        id=cid,
        name=name,
        aliases=json.dumps(aliases),
        definition=definition,
        concept_type=concept_type,
        domain=domain,
        importance=importance,
        epoch_discovered=epoch_discovered,
        epoch_last_updated=epoch_last_updated,
    )


def _make_session(existing_records=None):
    """Return a context-manager-compatible mock session pre-loaded with records."""
    if existing_records is None:
        existing_records = []
    exec_result = MagicMock()
    exec_result.all.return_value = existing_records
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = exec_result
    return session


# ── _extract_from_chunk ───────────────────────────────────────────────────────


def _make_rich_result(concepts=None):
    """Build a rich extraction result dict for testing."""
    return {
        "concepts": concepts or [],
        "parameters": [],
        "mechanisms": [],
        "relationships": [],
        "gaps": [],
    }


def test_merge_concept_records_new_concepts():
    """Two new records are inserted when the DB is empty; returns 2."""
    new_records = [
        _make_concept_record(cid="ald", name="ALD", aliases=[]),
        _make_concept_record(cid="cvd", name="CVD", aliases=[]),
    ]
    mock_session = _make_session(existing_records=[])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        count, _redirect = mod.merge_concept_records(new_records, epoch=1)

    assert count == 2
    assert mock_session.add.call_count == 2
    mock_session.commit.assert_called_once()


def test_merge_concept_records_dedup_by_slug():
    """An incoming record whose slug matches an existing record is not inserted."""
    existing = _make_concept_record(cid="ald", name="Atomic Layer Deposition", epoch_last_updated=1)
    incoming = _make_concept_record(cid="ald", name="ALD technique", aliases=[])
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        count, _redirect = mod.merge_concept_records([incoming], epoch=2)

    assert count == 0
    # The existing record should have been updated and re-added
    assert existing.epoch_last_updated == 2
    mock_session.add.assert_called_once_with(existing)
    mock_session.commit.assert_called_once()


def test_merge_concept_records_dedup_updates_epoch():
    """A slug match updates epoch_last_updated on the existing record."""
    existing = _make_concept_record(cid="ald", name="ALD", aliases=[], epoch_last_updated=1)
    incoming = _make_concept_record(cid="ald", name="ALD", aliases=[], epoch_last_updated=1)
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        _count, _redirect = mod.merge_concept_records([incoming], epoch=7)

    assert existing.epoch_last_updated == 7


def test_merge_concept_records_dedup_by_alias():
    """A record whose alias overlaps an existing record's aliases is not re-inserted."""
    existing = _make_concept_record(
        cid="atomic_layer_deposition",
        name="Atomic Layer Deposition",
        aliases=["ALD"],
    )
    # New record has a different slug but shares the "ALD" alias
    incoming = _make_concept_record(
        cid="ald_process",
        name="ALD Process",
        aliases=["ALD"],
    )
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        count, _redirect = mod.merge_concept_records([incoming], epoch=2)

    assert count == 0
    assert existing.epoch_last_updated == 2


def test_merge_concept_records_returns_redirect_map():
    """Redirect map shows input slug -> canonical slug when merged."""
    existing = _make_concept_record(
        cid="atomic_layer_deposition",
        name="Atomic Layer Deposition",
        aliases=["ALD"],
    )
    incoming = _make_concept_record(
        cid="ald_process",
        name="ALD Process",
        aliases=["ALD"],
    )
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        _count, redirect = mod.merge_concept_records([incoming], epoch=2)

    # ald_process was merged into atomic_layer_deposition
    assert redirect["ald_process"] == "atomic_layer_deposition"


def test_merge_concept_records_identity_for_new():
    """New concepts get identity mapping in redirect map."""
    mock_session = _make_session(existing_records=[])
    incoming = _make_concept_record(cid="cvd", name="CVD", aliases=[])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        _count, redirect = mod.merge_concept_records([incoming], epoch=1)

    assert redirect["cvd"] == "cvd"


def test_merge_concept_records_backfills_definition():
    """An existing record with no definition gets the incoming definition."""
    existing = _make_concept_record(cid="ald", name="ALD", aliases=[], definition="")
    incoming = _make_concept_record(
        cid="ald", name="ALD", aliases=[], definition="A deposition technique."
    )
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        _count, _redirect = mod.merge_concept_records([incoming], epoch=1)

    assert existing.definition == "A deposition technique."


def test_merge_concept_records_merges_aliases():
    """New aliases from an incoming record are merged into the existing record's aliases."""
    existing = _make_concept_record(cid="ald", name="ALD", aliases=["ALD"])
    incoming = _make_concept_record(cid="ald", name="ALD", aliases=["atomic layer dep."])
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session):
        _count, _redirect = mod.merge_concept_records([incoming], epoch=1)

    merged = existing.parsed_aliases
    assert "ALD" in merged
    assert "atomic layer dep." in merged


def test_merge_concept_records_empty_input():
    """Returns 0 immediately when called with an empty list."""
    mock_session = _make_session()

    with patch("wikify.wiki.concepts.merge.get_session", return_value=mock_session) as mock_gs:
        count, _redirect = mod.merge_concept_records([], epoch=1)

    assert count == 0
    mock_gs.assert_not_called()


# ── get_concept_by_name ───────────────────────────────────────────────────────


def test_get_concept_by_name_slug_match():
    """Slug match returns the record directly via session.get()."""
    existing = _make_concept_record(cid="ald", name="ALD")
    mock_session = _make_session(existing_records=[existing])
    # session.get() returns the record when the slug matches
    mock_session.get.return_value = existing

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.get_concept_by_name("ALD")

    assert result is existing
    # slugify("ALD") == "ald"
    mock_session.get.assert_called_once_with(ConceptRecord, "ald")


def test_get_concept_by_name_alias_match():
    """Falls back to alias scan when slug lookup returns None."""
    existing = _make_concept_record(
        cid="atomic_layer_deposition",
        name="Atomic Layer Deposition",
        aliases=["ALD"],
    )
    mock_session = _make_session(existing_records=[existing])
    # slug "ald" does not match primary key "atomic_layer_deposition"
    mock_session.get.return_value = None

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.get_concept_by_name("ALD")

    assert result is existing


def test_get_concept_by_name_name_match():
    """Falls back to name comparison (case-insensitive) when slug is not a primary key."""
    existing = _make_concept_record(
        cid="atomic_layer_deposition",
        name="Atomic Layer Deposition",
        aliases=[],
    )
    mock_session = _make_session(existing_records=[existing])
    mock_session.get.return_value = None

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.get_concept_by_name("atomic layer deposition")

    assert result is existing


def test_get_concept_by_name_returns_none_when_not_found():
    """Returns None when neither slug, name, nor alias matches."""
    mock_session = _make_session(existing_records=[])
    mock_session.get.return_value = None

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.get_concept_by_name("Unknown Concept")

    assert result is None


# ── list_concepts ─────────────────────────────────────────────────────────────


def test_list_concepts_filters_by_domain():
    """Only records whose domain contains the requested domain string are returned."""
    records = [
        _make_concept_record(cid="r1", name="R1", domain="physics", importance=0.8),
        _make_concept_record(cid="r2", name="R2", domain="chemistry", importance=0.6),
        _make_concept_record(cid="r3", name="R3", domain="physics", importance=0.4),
    ]
    mock_session = _make_session(existing_records=records)

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.list_concepts(domain="physics")

    ids = [r.id for r in result]
    assert "r1" in ids
    assert "r3" in ids
    assert "r2" not in ids


def test_list_concepts_filters_by_min_importance():
    """Only records meeting the importance threshold are returned."""
    # We need to make the session.exec() respect the WHERE clause. Since we are
    # using a mock, pre-filter the records to simulate SQLModel behaviour.
    all_records = [
        _make_concept_record(cid="r1", name="R1", importance=0.8),
        _make_concept_record(cid="r2", name="R2", importance=0.3),
        _make_concept_record(cid="r3", name="R3", importance=0.6),
    ]
    # Simulate SQL filter: only return records with importance >= 0.5
    filtered = [r for r in all_records if r.importance >= 0.5]
    exec_result = MagicMock()
    exec_result.all.return_value = filtered
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value = exec_result

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.list_concepts(min_importance=0.5)

    ids = [r.id for r in result]
    assert "r1" in ids
    assert "r3" in ids
    assert "r2" not in ids


def test_list_concepts_combined_domain_and_importance():
    """Domain and importance filters are both applied.

    The SQL WHERE clause filters by importance; the Python post-filter handles domain.
    The mock simulates the SQL result by pre-filtering on importance (>= 0.5), so r2
    (importance=0.3) is excluded at the DB level.  The domain post-filter then leaves
    only r1 (physics) and drops r3 (chemistry).
    """
    all_records = [
        _make_concept_record(cid="r1", name="R1", domain="physics", importance=0.8),
        _make_concept_record(cid="r2", name="R2", domain="physics", importance=0.3),
        _make_concept_record(cid="r3", name="R3", domain="chemistry", importance=0.7),
    ]
    # Simulate SQLModel WHERE importance >= 0.5 at the DB level
    importance_filtered = [r for r in all_records if r.importance >= 0.5]
    exec_result = MagicMock()
    exec_result.all.return_value = importance_filtered
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value = exec_result

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.list_concepts(domain="physics", min_importance=0.5)

    assert len(result) == 1
    assert result[0].id == "r1"


def test_list_concepts_sorted_by_importance_descending():
    """Results are ordered by importance, highest first."""
    records = [
        _make_concept_record(cid="low", name="Low", domain="", importance=0.2),
        _make_concept_record(cid="high", name="High", domain="", importance=0.9),
        _make_concept_record(cid="mid", name="Mid", domain="", importance=0.5),
    ]
    exec_result = MagicMock()
    exec_result.all.return_value = records
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value = exec_result

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.list_concepts()

    assert result[0].id == "high"
    assert result[1].id == "mid"
    assert result[2].id == "low"


def test_list_concepts_no_filter_returns_all():
    """Calling list_concepts() with defaults returns all records."""
    records = [
        _make_concept_record(cid="a", name="A", importance=0.0),
        _make_concept_record(cid="b", name="B", importance=0.0),
    ]
    exec_result = MagicMock()
    exec_result.all.return_value = records
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value = exec_result

    with patch("wikify.wiki.concepts.records.get_session", return_value=mock_session):
        result = mod.list_concepts()

    assert len(result) == 2


# ── _fuzzy_match_quote ───────────────────────────────────────────────────────


def test_fuzzy_match_quote_exact():
    """Exact substring match returns True."""
    assert mod.fuzzy_match_quote("ALD is a technique", "ALD is a technique used")


def test_fuzzy_match_quote_case_insensitive():
    """Case difference still matches."""
    assert mod.fuzzy_match_quote("ALD Is A Technique", "ald is a technique used")


def test_fuzzy_match_quote_whitespace():
    """Extra whitespace in source still matches."""
    assert mod.fuzzy_match_quote("ALD is great", "ALD  is   great today")


def test_fuzzy_match_quote_punctuation():
    """Punctuation differences still match."""
    assert mod.fuzzy_match_quote("ALD, is great.", "ALD is great today")


def test_fuzzy_match_quote_no_match():
    """Non-matching quote returns False."""
    assert not mod.fuzzy_match_quote("CVD is better", "ALD is a technique")


def test_fuzzy_match_quote_empty():
    """Empty quote or source returns False."""
    assert not mod.fuzzy_match_quote("", "some text")
    assert not mod.fuzzy_match_quote("quote", "")


# ── store_evidence ───────────────────────────────────────────────────────────


def test_store_evidence_creates_rows():
    """store_evidence creates ConceptEvidence rows for concepts with evidence."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "ALD is a technique for thin film deposition",
                "concepts": [
                    {
                        "name": "ALD",
                        "type": "technique",
                        "evidence": "ALD is a technique",
                    }
                ],
                "parameters": [],
                "mechanisms": [],
                "relationships": [],
                "gaps": [],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_evidence(rich, epoch=1)

    assert count == 1
    mock_session.add.assert_called_once()
    row = mock_session.add.call_args[0][0]
    assert row.concept_id == "ald"
    assert row.verified is True


def test_store_evidence_marks_unverified():
    """Evidence not found in chunk is marked verified=False."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "Some completely different text",
                "concepts": [
                    {
                        "name": "ALD",
                        "type": "technique",
                        "evidence": "ALD is a technique for deposition",
                    }
                ],
                "parameters": [],
                "mechanisms": [],
                "relationships": [],
                "gaps": [],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_evidence(rich, epoch=1)

    assert count == 1
    row = mock_session.add.call_args[0][0]
    assert row.verified is False


def test_store_evidence_skips_empty():
    """Concepts without evidence quotes are skipped."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "text",
                "concepts": [{"name": "ALD", "type": "technique", "evidence": ""}],
                "parameters": [],
                "mechanisms": [],
                "relationships": [],
                "gaps": [],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_evidence(rich, epoch=1)

    assert count == 0


# ── store_gaps ───────────────────────────────────────────────────────────────


def test_store_gaps_creates_rows():
    """store_gaps creates ExtractionGap rows for gaps with descriptions."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "text",
                "concepts": [],
                "parameters": [],
                "mechanisms": [],
                "relationships": [],
                "gaps": [
                    {
                        "description": "device reliability data",
                        "suggested_type": "reliability_metric",
                    },
                    {
                        "description": "process window info",
                        "suggested_type": "process_param",
                    },
                ],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_gaps(rich, epoch=1)

    assert count == 2
    assert mock_session.add.call_count == 2


def test_store_gaps_skips_empty_description():
    """Gaps without a description are skipped."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "text",
                "concepts": [],
                "parameters": [],
                "mechanisms": [],
                "relationships": [],
                "gaps": [
                    {"description": "", "suggested_type": "something"},
                ],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_gaps(rich, epoch=1)

    assert count == 0


# ── _identify_deepening_chunks ──────────────────────────────────────────────


def test_store_parameters_creates_rows():
    """store_parameters creates ParameterExtraction rows."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "text",
                "concepts": [],
                "parameters": [
                    {
                        "concept_name": "ALD",
                        "parameter_name": "growth rate",
                        "value": "1.0",
                        "unit": "A/cycle",
                        "conditions": "250C substrate",
                        "evidence": "growth rate of 1.0",
                    }
                ],
                "mechanisms": [],
                "relationships": [],
                "gaps": [],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_parameters(rich, epoch=1)

    assert count == 1
    row = mock_session.add.call_args[0][0]
    assert row.concept_id == "ald"
    assert row.parameter_name == "growth rate"
    assert row.value == "1.0"
    assert row.unit == "A/cycle"


def test_store_parameters_skips_empty():
    """Parameters without name or value are skipped."""
    rich = {
        "paper1": [
            {
                "_chunk_id": "c1",
                "_paper_id": "paper1",
                "_chunk_content": "text",
                "concepts": [],
                "parameters": [
                    {
                        "concept_name": "ALD",
                        "parameter_name": "",
                        "value": "1.0",
                        "unit": "",
                    },
                    {
                        "concept_name": "ALD",
                        "parameter_name": "rate",
                        "value": "",
                        "unit": "",
                    },
                ],
                "mechanisms": [],
                "relationships": [],
                "gaps": [],
            }
        ]
    }

    mock_session = _make_session()
    with patch("wikify.wiki.concepts.evidence.get_session", return_value=mock_session):
        count = mod.store_parameters(rich, epoch=1)

    assert count == 0
