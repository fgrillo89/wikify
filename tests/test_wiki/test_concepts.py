"""Tests for wiki/concepts.py -- Haiku-based concept discovery pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import wikify.wiki.concepts as mod
from wikify.store.models import ConceptRecord

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_chunk(chunk_id="chunk1", paper_id="paper1", content="Some text about ALD techniques and HfO2 thin film deposition methods used in research.", chunk_index=0):
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


def test_extract_from_chunk_basic():
    """Returns one ConceptRecord with correct fields when LLM succeeds."""
    chunk = _make_chunk()
    llm_response = [
        {
            "name": "Atomic Layer Deposition",
            "type": "technique",
            "aliases": ["ALD"],
            "definition": "A thin film deposition method",
        }
    ]

    with patch("wikify.wiki.concepts.complete_json", return_value=llm_response):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert len(records) == 1
    rec = records[0]
    assert rec.id == "atomic_layer_deposition"
    assert rec.name == "Atomic Layer Deposition"
    assert rec.concept_type == "technique"
    assert rec.definition == "A thin film deposition method"
    assert "ALD" in rec.parsed_aliases


def test_extract_from_chunk_filters_invalid_type():
    """An invalid concept_type is replaced with empty string; record is still created."""
    chunk = _make_chunk()
    llm_response = [
        {
            "name": "Some Term",
            "type": "invalid_type",
            "aliases": [],
            "definition": "A generic term.",
        }
    ]

    with patch("wikify.wiki.concepts.complete_json", return_value=llm_response):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert len(records) == 1
    assert records[0].concept_type == ""
    assert records[0].name == "Some Term"


def test_extract_from_chunk_handles_empty_response():
    """Returns empty list when the LLM returns an empty array."""
    chunk = _make_chunk()

    with patch("wikify.wiki.concepts.complete_json", return_value=[]):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert records == []


def test_extract_from_chunk_handles_llm_failure():
    """Returns empty list (no crash) when the LLM call raises an exception."""
    chunk = _make_chunk()

    with patch("wikify.wiki.concepts.complete_json", side_effect=RuntimeError("LLM down")):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert records == []


def test_extract_from_chunk_handles_non_list_response():
    """Returns empty list when the LLM returns something other than a list."""
    chunk = _make_chunk()

    with patch("wikify.wiki.concepts.complete_json", return_value={"error": "oops"}):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert records == []


def test_extract_from_chunk_skips_items_missing_name():
    """Items without a 'name' key are silently skipped."""
    chunk = _make_chunk()
    llm_response = [
        {"type": "technique", "aliases": [], "definition": "No name here."},
        {"name": "Valid Concept", "type": "method", "aliases": [], "definition": "Has a name."},
    ]

    with patch("wikify.wiki.concepts.complete_json", return_value=llm_response):
        records = mod._extract_from_chunk(chunk, prior_context=[], model="test-model")

    assert len(records) == 1
    assert records[0].name == "Valid Concept"


# ── extract_concepts_from_source ─────────────────────────────────────────────


def test_extract_concepts_from_source_threads_context():
    """prior_context passed to each chunk should be the names from the previous chunk."""
    chunks = [
        _make_chunk(chunk_id=f"c{i}", chunk_index=i, content=f"Chunk {i} discusses atomic layer deposition of HfO2 thin films for memristor applications")
        for i in range(3)
    ]

    # Each call returns one record with a unique name
    side_effects = [
        [_make_concept_record(cid="ald", name="ALD")],
        [_make_concept_record(cid="tma", name="TMA", aliases=[])],
        [_make_concept_record(cid="h2o", name="H2O", aliases=[])],
    ]

    with patch.object(mod, "_extract_from_chunk", side_effect=side_effects) as mock_extract:
        records = mod.extract_concepts_from_source(
            source_id="paper1", chunks=chunks, epoch=1, model="test-model"
        )

    assert mock_extract.call_count == 3

    # First chunk: no prior context
    assert mock_extract.call_args_list[0] == call(chunks[0], prior_context=[], model="test-model")
    # Second chunk: prior = names from first chunk's output
    assert mock_extract.call_args_list[1] == call(chunks[1], prior_context=["ALD"], model="test-model")
    # Third chunk: prior = names from second chunk's output
    assert mock_extract.call_args_list[2] == call(chunks[2], prior_context=["TMA"], model="test-model")

    assert len(records) == 3


def test_extract_concepts_from_source_stamps_epoch():
    """All returned records have epoch_discovered and epoch_last_updated set."""
    chunks = [_make_chunk()]
    side_effects = [
        [_make_concept_record(epoch_discovered=0, epoch_last_updated=0)],
    ]

    with patch.object(mod, "_extract_from_chunk", side_effect=side_effects):
        records = mod.extract_concepts_from_source(
            source_id="paper1", chunks=chunks, epoch=5, model="test-model"
        )

    assert all(r.epoch_discovered == 5 for r in records)
    assert all(r.epoch_last_updated == 5 for r in records)


def test_extract_concepts_from_source_empty_chunks():
    """Returns empty list when no chunks are provided."""
    with patch.object(mod, "_extract_from_chunk") as mock_extract:
        records = mod.extract_concepts_from_source(
            source_id="paper1", chunks=[], epoch=1, model="test-model"
        )

    assert records == []
    mock_extract.assert_not_called()


# ── merge_concept_records ─────────────────────────────────────────────────────


def test_merge_concept_records_new_concepts():
    """Two new records are inserted when the DB is empty; returns 2."""
    new_records = [
        _make_concept_record(cid="ald", name="ALD", aliases=[]),
        _make_concept_record(cid="cvd", name="CVD", aliases=[]),
    ]
    mock_session = _make_session(existing_records=[])

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        count = mod.merge_concept_records(new_records, epoch=1)

    assert count == 2
    assert mock_session.add.call_count == 2
    mock_session.commit.assert_called_once()


def test_merge_concept_records_dedup_by_slug():
    """An incoming record whose slug matches an existing record is not inserted."""
    existing = _make_concept_record(cid="ald", name="Atomic Layer Deposition", epoch_last_updated=1)
    incoming = _make_concept_record(cid="ald", name="ALD technique", aliases=[])
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        count = mod.merge_concept_records([incoming], epoch=2)

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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        mod.merge_concept_records([incoming], epoch=7)

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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        count = mod.merge_concept_records([incoming], epoch=2)

    assert count == 0
    assert existing.epoch_last_updated == 2


def test_merge_concept_records_backfills_definition():
    """An existing record with no definition gets the incoming definition."""
    existing = _make_concept_record(cid="ald", name="ALD", aliases=[], definition="")
    incoming = _make_concept_record(cid="ald", name="ALD", aliases=[], definition="A deposition technique.")
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        mod.merge_concept_records([incoming], epoch=1)

    assert existing.definition == "A deposition technique."


def test_merge_concept_records_merges_aliases():
    """New aliases from an incoming record are merged into the existing record's aliases."""
    existing = _make_concept_record(cid="ald", name="ALD", aliases=["ALD"])
    incoming = _make_concept_record(cid="ald", name="ALD", aliases=["atomic layer dep."])
    mock_session = _make_session(existing_records=[existing])

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        mod.merge_concept_records([incoming], epoch=1)

    merged = existing.parsed_aliases
    assert "ALD" in merged
    assert "atomic layer dep." in merged


def test_merge_concept_records_empty_input():
    """Returns 0 immediately when called with an empty list."""
    mock_session = _make_session()

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session) as mock_gs:
        count = mod.merge_concept_records([], epoch=1)

    assert count == 0
    mock_gs.assert_not_called()


# ── get_concept_by_name ───────────────────────────────────────────────────────


def test_get_concept_by_name_slug_match():
    """Slug match returns the record directly via session.get()."""
    existing = _make_concept_record(cid="ald", name="ALD")
    mock_session = _make_session(existing_records=[existing])
    # session.get() returns the record when the slug matches
    mock_session.get.return_value = existing

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        result = mod.get_concept_by_name("atomic layer deposition")

    assert result is existing


def test_get_concept_by_name_returns_none_when_not_found():
    """Returns None when neither slug, name, nor alias matches."""
    mock_session = _make_session(existing_records=[])
    mock_session.get.return_value = None

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
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

    with patch("wikify.wiki.concepts.get_session", return_value=mock_session):
        result = mod.list_concepts()

    assert len(result) == 2
