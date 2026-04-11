"""Run-scoped artifact store keyed by ``ArtifactRef``.

Workflow nodes read inputs and write outputs through this store rather than
sharing mutable globals. The store is the single audit trail for what each
DAG run produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wikify.wiki.discovery.contracts import ArtifactRef


class ArtifactBindingError(KeyError):
    """Raised when a node tries to read an artifact that has not been produced."""


@dataclass
class ArtifactStore:
    """In-memory typed artifact store scoped to a single DAG run."""

    _data: dict[str, Any] = field(default_factory=dict)
    _kinds: dict[str, str] = field(default_factory=dict)

    def put(self, ref: ArtifactRef, value: Any) -> None:
        self._data[ref.key] = value
        self._kinds[ref.key] = ref.kind

    def get(self, ref: ArtifactRef) -> Any:
        if ref.key not in self._data:
            raise ArtifactBindingError(
                f"artifact {ref} not produced before being read"
            )
        actual_kind = self._kinds[ref.key]
        if actual_kind != ref.kind:
            raise ArtifactBindingError(
                f"artifact {ref.key} kind mismatch: expected {ref.kind}, got {actual_kind}"
            )
        return self._data[ref.key]

    def has(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)
