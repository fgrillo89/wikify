"""YAML workflow configuration loader.

Workflow YAML files are validated into typed ``DagRunSpec`` objects so the
runtime never executes raw YAML directly. The schema is intentionally
small and Hydra-free; richer composition can be added later if it clearly
reduces complexity.

Example workflow YAML::

    workflow_id: default_publication
    strategy_id: synopsis_first_publication
    nodes:
      - id: profile
        impl: profile_document
        inputs:  {document: {kind: document, key: document}}
        outputs: {profile:  {kind: profile,  key: profile}}
      - id: plan
        impl: plan_units
        inputs:
          document: {kind: document, key: document}
          profile:  {kind: profile,  key: profile}
        outputs: {units: {kind: units, key: units}}
        params: {chunk_budget: 64}
      ...
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from wikify.wiki.discovery.contracts import ArtifactRef, DagNodeSpec, DagRunSpec


class WorkflowConfigError(ValueError):
    """Raised when a workflow YAML cannot be parsed into a valid spec."""


def _parse_ref(raw: Any, where: str) -> ArtifactRef:
    if not isinstance(raw, dict):
        raise WorkflowConfigError(f"{where}: expected mapping, got {type(raw).__name__}")
    try:
        return ArtifactRef(
            kind=str(raw["kind"]),
            key=str(raw["key"]),
            cardinality=str(raw.get("cardinality", "one")),
        )
    except KeyError as exc:
        raise WorkflowConfigError(f"{where}: missing field {exc}") from exc


def parse_workflow(data: dict[str, Any], *, source: str = "<inline>") -> DagRunSpec:
    if not isinstance(data, dict):
        raise WorkflowConfigError("workflow root must be a mapping")
    try:
        workflow_id = str(data["workflow_id"])
        strategy_id = str(data["strategy_id"])
        raw_nodes = data["nodes"]
    except KeyError as exc:
        raise WorkflowConfigError(f"missing top-level field {exc}") from exc
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise WorkflowConfigError("workflow.nodes must be a non-empty list")

    nodes: list[DagNodeSpec] = []
    for i, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise WorkflowConfigError(f"node[{i}] must be a mapping")
        try:
            node_id = str(raw["id"])
            impl = str(raw["impl"])
        except KeyError as exc:
            raise WorkflowConfigError(f"node[{i}] missing field {exc}") from exc

        inputs = {
            slot: _parse_ref(ref, f"node[{node_id}].inputs.{slot}")
            for slot, ref in (raw.get("inputs") or {}).items()
        }
        outputs = {
            slot: _parse_ref(ref, f"node[{node_id}].outputs.{slot}")
            for slot, ref in (raw.get("outputs") or {}).items()
        }
        params = dict(raw.get("params") or {})
        depends_on = tuple(str(d) for d in (raw.get("depends_on") or ()))
        nodes.append(
            DagNodeSpec(
                node_id=node_id,
                impl=impl,
                inputs=inputs,
                outputs=outputs,
                params=params,
                depends_on=depends_on,
            )
        )

    canonical = yaml.safe_dump(data, sort_keys=True).encode("utf-8")
    config_hash = hashlib.sha256(canonical).hexdigest()[:16]

    return DagRunSpec(
        workflow_id=workflow_id,
        nodes=tuple(nodes),
        strategy_id=strategy_id,
        config_hash=config_hash,
        config_source=source,
        params=dict(data.get("params") or {}),
    )


def load_workflow_yaml(path: str | Path) -> DagRunSpec:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return parse_workflow(data, source=str(p))
