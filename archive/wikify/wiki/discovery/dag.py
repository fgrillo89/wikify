"""DAG validation for discovery workflows.

Workflows are described as a tuple of ``DagNodeSpec`` objects with declared
typed inputs and outputs. ``validate_dag`` enforces:

- unique node ids
- ``depends_on`` references resolve
- no cycles
- every input is produced by some upstream node, except inputs marked as
  workflow-level seed artifacts (passed to the executor at run start)
- artifact ``kind`` matches between producer and consumer
"""

from __future__ import annotations

from collections import defaultdict, deque

from wikify.wiki.discovery.contracts import ArtifactRef, DagNodeSpec, DagRunSpec


class DagValidationError(ValueError):
    """Raised when a workflow's DAG is structurally invalid."""


def validate_dag(
    spec: DagRunSpec,
    *,
    seed_artifacts: dict[str, ArtifactRef] | None = None,
) -> list[str]:
    """Validate ``spec`` and return a topologically sorted list of node ids.

    ``seed_artifacts`` maps an artifact key to its declared ``ArtifactRef``;
    these are treated as already-produced inputs the executor will inject.
    """

    seeds: dict[str, ArtifactRef] = dict(seed_artifacts or {})
    nodes = spec.nodes
    by_id: dict[str, DagNodeSpec] = {}
    for node in nodes:
        if node.node_id in by_id:
            raise DagValidationError(f"duplicate node id: {node.node_id}")
        by_id[node.node_id] = node

    # Resolve depends_on edges + implicit edges from input artifacts.
    producers: dict[str, str] = {key: "<seed>" for key in seeds}
    producer_kinds: dict[str, str] = {key: ref.kind for key, ref in seeds.items()}
    for node in nodes:
        for slot, ref in node.outputs.items():
            if ref.key in producers:
                raise DagValidationError(
                    f"artifact key '{ref.key}' produced by both "
                    f"{producers[ref.key]} and {node.node_id}"
                )
            producers[ref.key] = node.node_id
            producer_kinds[ref.key] = ref.kind

    edges: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {n.node_id: 0 for n in nodes}

    def add_edge(src: str, dst: str) -> None:
        if src == "<seed>" or src == dst:
            return
        if dst not in edges[src]:
            edges[src].add(dst)
            indegree[dst] = indegree.get(dst, 0) + 1

    for node in nodes:
        for dep in node.depends_on:
            if dep not in by_id:
                raise DagValidationError(
                    f"node {node.node_id} depends_on unknown node {dep}"
                )
            add_edge(dep, node.node_id)
        for slot, ref in node.inputs.items():
            if ref.key not in producers:
                raise DagValidationError(
                    f"node {node.node_id} input '{slot}' references "
                    f"unproduced artifact '{ref.key}'"
                )
            if producer_kinds[ref.key] != ref.kind:
                raise DagValidationError(
                    f"node {node.node_id} input '{slot}' kind mismatch: "
                    f"expected {ref.kind}, producer emits {producer_kinds[ref.key]}"
                )
            add_edge(producers[ref.key], node.node_id)

    # Kahn topological sort to detect cycles.
    queue: deque[str] = deque(nid for nid, d in indegree.items() if d == 0)
    order: list[str] = []
    indeg = dict(indegree)
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for nxt in edges.get(nid, ()):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)

    if len(order) != len(nodes):
        unresolved = [nid for nid, d in indeg.items() if d > 0]
        raise DagValidationError(f"cycle detected in workflow involving: {sorted(unresolved)}")

    return order
