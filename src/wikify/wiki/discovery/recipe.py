"""Recipe layer — user-facing wikification config.

A *recipe* is a high-level, human-editable description of a wiki run
organized around conceptual steps (concept identification, people
identification, frontier strategy, article writing, cross-linking, ...)
rather than DAG node ids and artifact references.

Recipes live under ``src/wikify/wiki/recipes/<name>.yaml`` and are
compiled into a ``DagRunSpec`` by ``recipe_compiler.compile_recipe``.
The DAG layer remains the execution substrate; the recipe layer is
what users and orchestrating agents read and edit.

See ``docs/design/workflow-config-redesign.md`` for the broader plan.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

# Vendor-neutral model tier names. Resolved against ``llm.client.resolve_model_name``
# at runtime; the actual model id lives in ``config.Settings``.
KNOWN_MODEL_TIERS = frozenset(
    {
        "fast",
        "cheap",
        "map",
        "balanced",
        "default",
        "writer",
        "deep",
        "reasoning",
        "audit",
        "vision",
    }
)

# Frontier priority labels used by recipe configs.
#
# Note: priorities are consumed by workflow/node implementations.
# The parser validates labels so recipes stay self-documenting and
# consistent even when a specific priority mode is not yet fully wired.
KNOWN_FRONTIER_PRIORITIES = frozenset(
    {
        "section_tier",
        "recency",
        "weight",
        "hub_spoke",
    }
)

# Conceptual step kinds the recipe compiler knows how to lower into DAG nodes.
KNOWN_STEP_KINDS = frozenset(
    {
        "profile_documents",
        "identify_concepts",
        "identify_people",
        "identify_figures_tables",
        "consolidate",
        "persist_canonical",
        "cross_link",
        "write_articles",
        "maintain",
    }
)


class RecipeError(ValueError):
    """Raised when a recipe YAML cannot be parsed into a valid ``Recipe``."""


@dataclass(frozen=True)
class FrontierConfig:
    """Frontier scheduling strategy and budgets.

    ``strategy`` selects an entry in
    ``wiki.discovery.scheduler``-aware registries; today only
    ``eventual_coverage`` is supported.
    """

    strategy: str = "eventual_coverage"
    budget_per_epoch: int = 64
    exploration_rate: float = 0.05
    priority: str = "section_tier"
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepConfig:
    """One conceptual step in a recipe.

    Steps map to one or more DAG nodes. The compiler decides the
    mapping based on ``kind``; the recipe author does not write DAG
    nodes by hand.
    """

    # Human-readable step id used for DAG node ids, telemetry labels, and
    # `inputs_from` references. Defaults to `kind` when omitted in YAML.
    name: str
    kind: str  # one of KNOWN_STEP_KINDS
    model: str = "fast"  # tier name resolved by llm.client.resolve_model_name
    prompt: str | None = None  # path relative to repo root or absolute
    schema: str | None = None  # path to a JSON schema file
    style_guide: str | None = None
    units: tuple[str, ...] = ()  # ("chunk", "synopsis", "figure", "table", ...)
    multimodal: bool = False
    inputs_from: tuple[str, ...] = ()  # other step names whose outputs feed this one
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class Recipe:
    """User-facing wikification config.

    Compiled into a ``DagRunSpec`` by ``recipe_compiler.compile_recipe``.
    """

    recipe_id: str
    description: str
    frontier: FrontierConfig
    steps: tuple[StepConfig, ...]
    source_path: str = "<inline>"
    config_hash: str = ""


def _require(data: dict, key: str, where: str) -> Any:
    if key not in data:
        raise RecipeError(f"{where}: missing required field '{key}'")
    return data[key]


def _parse_frontier(data: dict[str, Any] | None) -> FrontierConfig:
    if not data:
        return FrontierConfig()
    if not isinstance(data, dict):
        raise RecipeError("frontier: must be a mapping")
    priority = str(data.get("priority", "section_tier"))
    if priority not in KNOWN_FRONTIER_PRIORITIES:
        raise RecipeError(
            f"frontier.priority: unknown value '{priority}'. "
            f"Known values: {sorted(KNOWN_FRONTIER_PRIORITIES)}"
        )
    return FrontierConfig(
        strategy=str(data.get("strategy", "eventual_coverage")),
        budget_per_epoch=int(data.get("budget_per_epoch", 64)),
        exploration_rate=float(data.get("exploration_rate", 0.05)),
        priority=priority,
        filters=dict(data.get("filters") or {}),
    )


def _validate_asset_path(
    raw: Any,
    *,
    field_name: str,
    step_name: str,
) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RecipeError(f"steps[{step_name}]: {field_name} must be a string path")
    value = raw.strip()
    if not value:
        raise RecipeError(f"steps[{step_name}]: {field_name} must not be empty")

    candidate = Path(value)
    resolved = candidate if candidate.is_absolute() else _PROJECT_ROOT / candidate
    if not resolved.exists():
        raise RecipeError(
            f"steps[{step_name}]: {field_name} file '{value}' does not exist "
            f"(resolved to '{resolved}')"
        )
    if not resolved.is_file():
        raise RecipeError(
            f"steps[{step_name}]: {field_name} path '{value}' is not a file "
            f"(resolved to '{resolved}')"
        )
    return value


def _parse_step(raw: Any, idx: int) -> StepConfig:
    if not isinstance(raw, dict):
        raise RecipeError(f"steps[{idx}]: must be a mapping")
    kind = str(_require(raw, "kind", f"steps[{idx}]"))
    name = str(raw.get("name", kind)).strip() or kind
    if kind not in KNOWN_STEP_KINDS:
        raise RecipeError(
            f"steps[{name}]: unknown kind '{kind}'. "
            f"Known kinds: {sorted(KNOWN_STEP_KINDS)}"
        )
    model = str(raw.get("model", "fast"))
    if model not in KNOWN_MODEL_TIERS and not model.strip():
        raise RecipeError(f"steps[{name}]: model must be a tier name or model id")

    prompt = _validate_asset_path(raw.get("prompt"), field_name="prompt", step_name=name)
    schema = _validate_asset_path(raw.get("schema"), field_name="schema", step_name=name)
    style_guide = _validate_asset_path(
        raw.get("style_guide"), field_name="style_guide", step_name=name
    )
    return StepConfig(
        name=name,
        kind=kind,
        model=model,
        prompt=prompt,
        schema=schema,
        style_guide=style_guide,
        units=tuple(raw.get("units") or ()),
        multimodal=bool(raw.get("multimodal", False)),
        inputs_from=tuple(raw.get("inputs_from") or ()),
        params=dict(raw.get("params") or {}),
        enabled=bool(raw.get("enabled", True)),
    )


def parse_recipe(data: dict[str, Any], *, source: str = "<inline>") -> Recipe:
    """Validate a parsed YAML mapping into a typed ``Recipe``."""

    if not isinstance(data, dict):
        raise RecipeError("recipe root must be a mapping")
    recipe_id = str(_require(data, "recipe_id", "recipe"))
    description = str(data.get("description", "")).strip()
    raw_steps = _require(data, "steps", "recipe")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise RecipeError("recipe.steps must be a non-empty list")

    steps = tuple(_parse_step(s, i) for i, s in enumerate(raw_steps))
    enabled_steps = [s for s in steps if s.enabled]
    seen: set[str] = set()
    for s in enabled_steps:
        if s.name in seen:
            raise RecipeError(f"duplicate step name: {s.name}")
        seen.add(s.name)
    for s in enabled_steps:
        for dep in s.inputs_from:
            if dep not in seen:
                # Allow forward refs only if the producing step appears later;
                # we already iterated in order, so a missing dep is invalid.
                if not any(other.name == dep for other in enabled_steps):
                    raise RecipeError(
                        f"step '{s.name}': inputs_from references unknown step '{dep}'"
                    )

    frontier = _parse_frontier(data.get("frontier"))
    canonical = yaml.safe_dump(data, sort_keys=True).encode("utf-8")
    config_hash = hashlib.sha256(canonical).hexdigest()[:16]

    return Recipe(
        recipe_id=recipe_id,
        description=description,
        frontier=frontier,
        steps=tuple(enabled_steps),
        source_path=source,
        config_hash=config_hash,
    )


def load_recipe_yaml(path: str | Path) -> Recipe:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return parse_recipe(data, source=str(p))


__all__ = [
    "FrontierConfig",
    "KNOWN_FRONTIER_PRIORITIES",
    "KNOWN_MODEL_TIERS",
    "KNOWN_STEP_KINDS",
    "Recipe",
    "RecipeError",
    "StepConfig",
    "load_recipe_yaml",
    "parse_recipe",
]
