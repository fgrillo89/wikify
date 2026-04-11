"""Node implementation registry.

Workflow YAMLs reference node implementations by string id. The registry
maps those ids to callables, allowing alternative implementations and
test-time mocks without touching workflow definitions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

NodeImpl = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
"""A node implementation: ``(inputs, params) -> outputs``."""


class NodeRegistry:
    def __init__(self) -> None:
        self._impls: dict[str, NodeImpl] = {}

    def register(self, name: str, impl: NodeImpl) -> None:
        if name in self._impls:
            raise ValueError(f"node impl already registered: {name}")
        self._impls[name] = impl

    def get(self, name: str) -> NodeImpl:
        try:
            return self._impls[name]
        except KeyError as exc:
            raise KeyError(f"unknown node impl: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._impls)


def default_registry() -> NodeRegistry:
    """Return a registry preloaded with the built-in discovery nodes."""

    from wikify.wiki.discovery import nodes as builtin

    reg = NodeRegistry()
    builtin.register_builtin_nodes(reg)
    return reg
