from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import wikify.wiki.concepts.discovery as mod


def _make_session(rows: list[object] | None = None) -> MagicMock:
    exec_result = MagicMock()
    exec_result.all.return_value = rows or []
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = exec_result
    return session


def test_discover_concepts_requires_extractor_by_default() -> None:
    with pytest.raises(RuntimeError, match="explicit extractor"):
        mod.discover_concepts(["paper-1"], epoch=1)


def test_discover_concepts_allows_echo_extractor_when_opted_in() -> None:
    session = _make_session(rows=[])
    with (
        patch("wikify.wiki.concepts.discovery.get_session", return_value=session),
        patch("wikify.wiki.concepts.discovery._chunks_for_paper", return_value=[MagicMock()]),
        patch("wikify.wiki.concepts.discovery._build_units", return_value=[MagicMock()]),
        patch("wikify.wiki.concepts.discovery._notes_to_concept_records", return_value=([], {})),
        patch("wikify.wiki.concepts.discovery.merge_concept_records", return_value=(0, {})),
        patch.object(mod.EchoExtractor, "extract", return_value=[]) as mock_echo_extract,
    ):
        mod.discover_concepts(["paper-1"], epoch=1, allow_echo_extractor=True)

    mock_echo_extract.assert_called_once()


def test_discover_concepts_uses_dag_path_and_merges_executor_notes() -> None:
    note = mod.ExtractionNote(
        note_id="note-1",
        document_id="paper-1",
        unit_ids=["chunk-1"],
        strategy_id="dag-strategy",
        node_id="resolve_candidates",
        content={
            "concepts": [
                {
                    "name": "Atomic Layer Deposition",
                    "type": "concept",
                    "aliases": ["ALD"],
                    "definition": "A thin-film deposition method.",
                    "evidence": "Repeated self-limiting surface reactions.",
                }
            ],
            "_chunk_id": "chunk-1",
            "_chunk_content": "Repeated self-limiting surface reactions.",
            "parameters": ["temperature"],
            "mechanisms": ["self-limiting surface reactions"],
            "relationships": ["surface chemistry"],
            "gaps": ["unknown precursor window"],
        },
    )
    concept = mod.ConceptRecord(
        id="atomic_layer_deposition",
        name="Atomic Layer Deposition",
        article_status="none",
        importance=0.7,
        epoch_discovered=7,
        epoch_last_updated=7,
    )
    compiled_spec = mod.DagRunSpec(
        workflow_id="recipe::test",
        nodes=tuple(),
        strategy_id="dag-strategy",
        config_hash="hash",
        config_source="<test>",
    )
    executor = MagicMock(name="dag_executor")
    executor.run.return_value = MagicMock(
        artifacts={"all_notes": [note]},
        timings=[
            MagicMock(node_id="profile_documents", duration_s=0.12),
            MagicMock(node_id="identify_concepts", duration_s=0.34),
        ],
    )
    session = _make_session(rows=[concept])
    extractor = MagicMock(name="extractor")

    with (
        patch("wikify.wiki.concepts.discovery.get_session", return_value=session),
        patch("wikify.wiki.concepts.discovery.merge_concept_records", return_value=(1, {}))
        as mock_merge,
        patch(
            "wikify.wiki.concepts.discovery._document_payload_for_paper",
            return_value={"id": "paper-1", "type": "publication", "chunks": []},
        ),
        patch(
            "wikify.wiki.concepts.discovery.EchoExtractor",
            side_effect=AssertionError("legacy extractor path should not be used"),
        ) as mock_echo,
    ):
        result = mod.discover_concepts(
            ["paper-1"],
            epoch=7,
            extractor=extractor,
            workflow_spec=compiled_spec,
            dag_executor=executor,
        )

    executor.run.assert_called_once()
    called_spec = executor.run.call_args.args[0]
    called_kwargs = executor.run.call_args.kwargs
    assert called_spec is not compiled_spec
    assert called_kwargs["seed_artifacts"]["document"][1]["id"] == "paper-1"
    mock_merge.assert_called_once()
    merged_records, merged_epoch = mock_merge.call_args.args
    assert merged_epoch == 7
    assert len(merged_records) == 1
    assert merged_records[0].name == "Atomic Layer Deposition"
    assert merged_records[0].concept_type == "concept"
    assert merged_records[0].epoch_discovered == 7
    assert merged_records[0].epoch_last_updated == 7
    assert result.concepts == [concept]
    assert result.telemetry["mode"] == "dag"
    assert result.telemetry["documents_total"] == 1
    assert result.telemetry["documents_processed"] == 1
    assert result.telemetry["units_processed"] == 1
    assert result.telemetry["units_deferred"] == 0
    assert result.telemetry["node_runs"]["profile_documents"] == 1
    assert result.telemetry["node_runs"]["identify_concepts"] == 1
    assert result.telemetry["node_timing_s"]["identify_concepts"] == pytest.approx(0.34)
    assert result.rich_extractions == {
        "paper-1": [
            {
                "_chunk_id": "chunk-1",
                "_paper_id": "paper-1",
                "_chunk_content": "Repeated self-limiting surface reactions.",
                "concepts": [note.content["concepts"][0]],
                "parameters": ["temperature"],
                "mechanisms": ["self-limiting surface reactions"],
                "relationships": ["surface chemistry"],
                "gaps": ["unknown precursor window"],
            }
        ]
    }
    mock_echo.assert_not_called()
