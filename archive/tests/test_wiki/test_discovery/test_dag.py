"""DAG validation tests covering cycles, missing deps, kind mismatch."""

from __future__ import annotations

import pytest

from wikify.wiki.discovery.contracts import ArtifactRef, DagNodeSpec, DagRunSpec
from wikify.wiki.discovery.dag import DagValidationError, validate_dag


def _spec(nodes):
    return DagRunSpec(
        workflow_id="t",
        nodes=tuple(nodes),
        strategy_id="s",
        config_hash="h",
        config_source="<test>",
    )


def test_topological_order_simple_chain():
    a = DagNodeSpec(
        node_id="a",
        impl="x",
        outputs={"out": ArtifactRef("x", "a_out")},
    )
    b = DagNodeSpec(
        node_id="b",
        impl="x",
        inputs={"i": ArtifactRef("x", "a_out")},
        outputs={"out": ArtifactRef("x", "b_out")},
    )
    order = validate_dag(_spec([b, a]))  # intentionally out of order
    assert order == ["a", "b"]


def test_duplicate_node_id_rejected():
    n = DagNodeSpec(node_id="a", impl="x")
    with pytest.raises(DagValidationError, match="duplicate"):
        validate_dag(_spec([n, n]))


def test_missing_input_artifact_rejected():
    n = DagNodeSpec(
        node_id="a",
        impl="x",
        inputs={"i": ArtifactRef("x", "missing")},
    )
    with pytest.raises(DagValidationError, match="unproduced"):
        validate_dag(_spec([n]))


def test_kind_mismatch_rejected():
    a = DagNodeSpec(node_id="a", impl="x", outputs={"o": ArtifactRef("foo", "k")})
    b = DagNodeSpec(node_id="b", impl="x", inputs={"i": ArtifactRef("bar", "k")})
    with pytest.raises(DagValidationError, match="kind mismatch"):
        validate_dag(_spec([a, b]))


def test_cycle_detected():
    a = DagNodeSpec(
        node_id="a",
        impl="x",
        inputs={"i": ArtifactRef("x", "b_out")},
        outputs={"o": ArtifactRef("x", "a_out")},
    )
    b = DagNodeSpec(
        node_id="b",
        impl="x",
        inputs={"i": ArtifactRef("x", "a_out")},
        outputs={"o": ArtifactRef("x", "b_out")},
    )
    with pytest.raises(DagValidationError, match="cycle"):
        validate_dag(_spec([a, b]))


def test_seed_artifacts_satisfy_inputs():
    n = DagNodeSpec(
        node_id="a",
        impl="x",
        inputs={"i": ArtifactRef("doc", "document")},
        outputs={"o": ArtifactRef("p", "profile")},
    )
    seeds = {"document": ArtifactRef("doc", "document")}
    order = validate_dag(_spec([n]), seed_artifacts=seeds)
    assert order == ["a"]


def test_unknown_depends_on_rejected():
    n = DagNodeSpec(node_id="a", impl="x", depends_on=("ghost",))
    with pytest.raises(DagValidationError, match="unknown node"):
        validate_dag(_spec([n]))
