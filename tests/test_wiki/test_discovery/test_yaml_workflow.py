"""End-to-end YAML workflow execution test.

Loads the bundled ``default_publication.yaml`` workflow, runs it against a
synthetic document, and verifies that node timings, multimodal usage,
config provenance, and coverage records are all reported.
"""

from __future__ import annotations

from pathlib import Path

from wikify.wiki.discovery.config import load_workflow_yaml, parse_workflow
from wikify.wiki.discovery.contracts import ArtifactRef, CoverageRecord
from wikify.wiki.discovery.executor import DagExecutor
from wikify.wiki.discovery.notes import InMemoryNoteStore
from wikify.wiki.discovery.planner import DiscoveryPlanner
from wikify.wiki.discovery.registry import default_registry
from wikify.wiki.discovery.strategies import default_strategies

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "wikify"
    / "wiki"
    / "discovery"
    / "workflows"
    / "default_publication.yaml"
)


def _doc():
    return {
        "id": "doc-1",
        "type": "publication",
        "sections": ["abstract", "methods", "results"],
        "modalities": ["text", "image", "table"],
        "synopsis": "stub synopsis",
        "chunks": [
            {"id": "c1", "text": "alpha", "section": "abstract"},
            {"id": "c2", "text": "beta", "section": "methods"},
            {"id": "c3", "text": "gamma", "section": "results"},
        ],
        "figures": [{"id": "f1", "caption": "fig caption", "image_path": "/tmp/x.png"}],
        "tables": [{"id": "t1", "caption": "tbl", "rows": [["a", "b"]]}],
    }


def test_default_publication_workflow_runs_end_to_end():
    spec = load_workflow_yaml(WORKFLOW_PATH)
    assert spec.workflow_id == "default_publication"
    assert spec.strategy_id == "synopsis_first_publication"
    assert spec.config_hash and spec.config_hash != ""

    executor = DagExecutor(default_registry())
    notes_sink = InMemoryNoteStore()

    # Inject params that depend on runtime state.
    nodes = list(spec.nodes)
    for i, n in enumerate(nodes):
        if n.impl == "persist_notes":
            nodes[i] = n.__class__(
                node_id=n.node_id,
                impl=n.impl,
                inputs=n.inputs,
                outputs=n.outputs,
                params={**n.params, "sink": notes_sink, "document_id": "doc-1"},
                depends_on=n.depends_on,
            )
    runtime_spec = spec.__class__(
        workflow_id=spec.workflow_id,
        nodes=tuple(nodes),
        strategy_id=spec.strategy_id,
        config_hash=spec.config_hash,
        config_source=spec.config_source,
        params=spec.params,
    )

    result = executor.run(
        runtime_spec,
        seed_artifacts={"document": (ArtifactRef("document", "document"), _doc())},
    )

    # All six nodes ran in topological order.
    assert result.order[0] == "profile"
    assert result.order[-1] == "persist"
    assert {t.node_id for t in result.timings} == {
        "profile",
        "plan",
        "extract_text",
        "extract_multimodal",
        "resolve",
        "persist",
    }
    assert all(t.duration_s >= 0 for t in result.timings)

    # Multimodal pass produced notes -> reported in observability surface.
    assert result.multimodal_used is True

    # Notes were persisted via the injected sink.
    assert len(notes_sink.all()) > 0

    # Coverage record was emitted and contains processed units.
    coverage: CoverageRecord = result.artifacts["coverage"]
    assert coverage.document_id == "doc-1"
    assert len(coverage.processed_unit_ids) > 0


def test_planner_routes_by_document_type():
    strategies = default_strategies()
    planner = DiscoveryPlanner(strategies)
    from wikify.wiki.discovery.contracts import DocumentProfile

    pub = DocumentProfile(document_id="d1", document_type="publication")
    slides = DocumentProfile(document_id="d2", document_type="slide_deck")
    notes = DocumentProfile(document_id="d3", document_type="markdown")

    assert planner.choose(pub).strategy_id == "synopsis_first_publication"
    assert planner.choose(slides).strategy_id == "multimodal_first_slides"
    assert planner.choose(notes).strategy_id == "all_unit_sweep"


def test_invalid_workflow_yaml_rejected():
    import pytest

    from wikify.wiki.discovery.config import WorkflowConfigError

    with pytest.raises(WorkflowConfigError):
        parse_workflow({"workflow_id": "x"})  # missing nodes/strategy_id
