"""Validated DAG executor for discovery workflows.

The executor validates the DAG, then walks nodes in topological order,
binding declared inputs from the artifact store, calling the registered
implementation, and writing declared outputs back into the store. It
records per-node timings and config provenance so observability surfaces
can report workflow id, strategy id, node timings, and config hash.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from wikify.wiki.discovery.artifacts import ArtifactStore
from wikify.wiki.discovery.contracts import ArtifactRef, DagRunSpec
from wikify.wiki.discovery.dag import validate_dag
from wikify.wiki.discovery.registry import NodeRegistry


@dataclass
class NodeTiming:
    node_id: str
    impl: str
    started_at: float
    duration_s: float


@dataclass
class DagExecutionResult:
    workflow_id: str
    strategy_id: str
    config_hash: str
    config_source: str
    order: list[str]
    timings: list[NodeTiming]
    artifacts: dict[str, Any]
    multimodal_used: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class DagExecutor:
    """Executes a ``DagRunSpec`` against a ``NodeRegistry``."""

    def __init__(self, registry: NodeRegistry) -> None:
        self.registry = registry

    def run(
        self,
        spec: DagRunSpec,
        *,
        seed_artifacts: dict[str, tuple[ArtifactRef, Any]] | None = None,
    ) -> DagExecutionResult:
        seeds = seed_artifacts or {}
        seed_refs = {key: ref for key, (ref, _) in seeds.items()}
        order = validate_dag(spec, seed_artifacts=seed_refs)

        store = ArtifactStore()
        for key, (ref, value) in seeds.items():
            store.put(ref, value)

        by_id = {n.node_id: n for n in spec.nodes}
        timings: list[NodeTiming] = []
        multimodal_used = False

        for nid in order:
            node = by_id[nid]
            impl = self.registry.get(node.impl)
            bound_inputs = {slot: store.get(ref) for slot, ref in node.inputs.items()}
            params = dict(node.params)
            params.setdefault("node_id", node.node_id)
            params.setdefault("strategy_id", spec.strategy_id)
            started = time.time()
            outputs = impl(bound_inputs, params)
            duration = time.time() - started
            timings.append(NodeTiming(nid, node.impl, started, duration))

            if node.impl == "extract_multimodal":
                mm = outputs.get("multimodal_notes") or []
                if mm:
                    multimodal_used = True

            for slot, ref in node.outputs.items():
                if slot not in outputs:
                    raise KeyError(
                        f"node {nid} ({node.impl}) did not return declared output '{slot}'"
                    )
                store.put(ref, outputs[slot])

        return DagExecutionResult(
            workflow_id=spec.workflow_id,
            strategy_id=spec.strategy_id,
            config_hash=spec.config_hash,
            config_source=spec.config_source,
            order=order,
            timings=timings,
            artifacts=store.snapshot(),
            multimodal_used=multimodal_used,
        )
