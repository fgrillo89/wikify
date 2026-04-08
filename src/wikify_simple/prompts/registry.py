"""Immutable prompt registry for role templates.

All ``*.yaml`` files in this directory are loaded once at import time
into a frozen dict keyed by ``Prompt.name``. Bindings still receive
only the prompt id via ``ContextEnvelope``; the registry exists so the
Python side can look up the canonical template text from one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Prompt:
    name: str
    role: str
    version: int
    input_schema: Any
    output_schema: Any
    prompt_template: str
    sampler_hint: str


def _load_all() -> MappingProxyType[str, Prompt]:
    items: dict[str, Prompt] = {}
    for path in sorted(_PROMPTS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"prompt file {path} did not parse to a mapping")
        for key in ("name", "role", "version", "prompt_template"):
            if key not in data:
                raise ValueError(f"prompt file {path} missing required field: {key}")
        p = Prompt(
            name=str(data["name"]),
            role=str(data["role"]),
            version=int(data["version"]),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
            prompt_template=str(data["prompt_template"]),
            sampler_hint=str(data.get("sampler_hint", "haiku")),
        )
        if not p.prompt_template.strip():
            raise ValueError(f"prompt file {path} has empty prompt_template")
        items[p.name] = p
    return MappingProxyType(items)


_REGISTRY: MappingProxyType[str, Prompt] = _load_all()


def load_prompt(name: str) -> Prompt:
    """Return the frozen ``Prompt`` for ``name`` or raise ``KeyError``."""
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise KeyError(f"no prompt registered with name {name!r}") from e


def all_prompts() -> MappingProxyType[str, Prompt]:
    return _REGISTRY
