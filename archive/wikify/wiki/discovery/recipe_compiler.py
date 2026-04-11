"""Recipe → DagRunSpec compiler.

Translates a high-level ``Recipe`` (the user-facing config) into a
``DagRunSpec`` (the executor's substrate). Each conceptual step maps to
one or more DAG nodes via a small dispatch table; prompts, schemas, and
model tiers travel through the node ``params`` so the executor can
expose them to the orchestrating agent.

This compiler is intentionally small. As more step kinds are
introduced, extend ``_LOWER`` rather than threading new logic through
``epoch.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from wikify.wiki.discovery.contracts import ArtifactRef, DagNodeSpec, DagRunSpec
from wikify.wiki.discovery.recipe import Recipe, StepConfig

# A lowering function takes the step plus a small context and returns the
# DAG node specs that implement it. Most step kinds map to a single node;
# composite steps (e.g. identify_concepts -> plan + extract + resolve +
# persist) emit several nodes.
LowerFn = Callable[[StepConfig, "_CompileCtx"], list[DagNodeSpec]]


class _CompileCtx:
    """Mutable context shared across step lowerings."""

    def __init__(self, recipe: Recipe) -> None:
        self.recipe = recipe
        self.produced: dict[str, ArtifactRef] = {}
        self.notes_keys: list[str] = []  # accumulator for consolidate step
        self.deferred_steps: list[dict[str, Any]] = []  # step kinds without a DAG impl yet

    def emit(self, ref: ArtifactRef) -> ArtifactRef:
        self.produced[ref.key] = ref
        return ref


def _step_params(
    step: StepConfig,
    frontier_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect every recipe-derived knob that the executor should expose."""

    params: dict[str, Any] = {
        "model": step.model,
        "prompt": step.prompt,
        "schema": step.schema,
        "units": list(step.units),
        "multimodal": step.multimodal,
        "step_name": step.name,
    }
    if step.style_guide:
        params["style_guide"] = step.style_guide
    params.update(step.params)
    if frontier_overrides:
        params["frontier"] = frontier_overrides
    return params


def _lower_profile(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    return [
        DagNodeSpec(
            node_id=step.name,
            impl="profile_document",
            inputs={"document": ArtifactRef("document", "document")},
            outputs={"profile": ctx.emit(ArtifactRef("profile", "profile"))},
            params=_step_params(step),
        )
    ]


def _lower_identify_concepts(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    plan_node = DagNodeSpec(
        node_id=f"{step.name}__plan",
        impl="plan_units",
        inputs={
            "document": ArtifactRef("document", "document"),
            "profile": ctx.produced.get("profile", ArtifactRef("profile", "profile")),
        },
        outputs={"units": ctx.emit(ArtifactRef("units", f"{step.name}__units"))},
        params={
            **_step_params(step, frontier_overrides=ctx.recipe.frontier.__dict__),
            "chunk_budget": ctx.recipe.frontier.budget_per_epoch,
        },
    )
    extract_node = DagNodeSpec(
        node_id=step.name,
        impl="extract_text",
        inputs={"units": ctx.produced[f"{step.name}__units"]},
        outputs={"text_notes": ctx.emit(ArtifactRef("notes", f"{step.name}__notes"))},
        params=_step_params(step),
    )
    ctx.notes_keys.append(f"{step.name}__notes")
    return [plan_node, extract_node]


def _lower_identify_people(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    # People extraction reuses the text-extraction node with a different
    # prompt/schema. The agent runtime routes by ``params["step_name"]``
    # and ``params["schema"]``.
    if "concept_units" not in ctx.produced:
        plan_node = DagNodeSpec(
            node_id=f"{step.name}__plan",
            impl="plan_units",
            inputs={
                "document": ArtifactRef("document", "document"),
                "profile": ctx.produced.get("profile", ArtifactRef("profile", "profile")),
            },
            outputs={"units": ctx.emit(ArtifactRef("units", f"{step.name}__units"))},
            params=_step_params(step),
        )
        units_ref = ctx.produced[f"{step.name}__units"]
        prelude = [plan_node]
    else:
        units_ref = ctx.produced["concept_units"]
        prelude = []

    extract_node = DagNodeSpec(
        node_id=step.name,
        impl="extract_text",
        inputs={"units": units_ref},
        outputs={"text_notes": ctx.emit(ArtifactRef("notes", f"{step.name}__notes"))},
        params=_step_params(step),
    )
    ctx.notes_keys.append(f"{step.name}__notes")
    return prelude + [extract_node]


def _lower_identify_multimodal(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    # Reuses the units already planned by identify_concepts if present.
    units_key = next(
        (k for k in ctx.produced if k.endswith("__units")),
        None,
    )
    if units_key is None:
        plan_node = DagNodeSpec(
            node_id=f"{step.name}__plan",
            impl="plan_units",
            inputs={
                "document": ArtifactRef("document", "document"),
                "profile": ctx.produced.get("profile", ArtifactRef("profile", "profile")),
            },
            outputs={"units": ctx.emit(ArtifactRef("units", f"{step.name}__units"))},
            params=_step_params(step),
        )
        units_ref = ctx.produced[f"{step.name}__units"]
        prelude = [plan_node]
    else:
        units_ref = ctx.produced[units_key]
        prelude = []

    extract_node = DagNodeSpec(
        node_id=step.name,
        impl="extract_multimodal",
        inputs={"units": units_ref},
        outputs={"multimodal_notes": ctx.emit(ArtifactRef("notes", f"{step.name}__notes"))},
        params=_step_params(step),
    )
    ctx.notes_keys.append(f"{step.name}__notes")
    return prelude + [extract_node]


def _lower_consolidate(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    # Consolidate ingests every notes artifact emitted so far and resolves
    # to candidate concepts. The compiler binds the inputs by name so the
    # executor can validate them.
    inputs: dict[str, ArtifactRef] = {}
    if not ctx.notes_keys:
        raise ValueError(
            f"step '{step.name}': consolidate has no upstream notes to consume; "
            "add an identify_* step before it"
        )
    # The resolve_candidates node accepts text_notes and multimodal_notes;
    # we map the first text-ish notes to text_notes and any multimodal to
    # multimodal_notes. Anything else goes through text_notes too.
    text_keys = [k for k in ctx.notes_keys if "multimodal" not in k and "figure" not in k]
    mm_keys = [k for k in ctx.notes_keys if k not in text_keys]
    if text_keys:
        inputs["text_notes"] = ctx.produced[text_keys[0]]
    if mm_keys:
        inputs["multimodal_notes"] = ctx.produced[mm_keys[0]]
    if "text_notes" not in inputs:
        # resolve_candidates expects text_notes; supply an empty alias by
        # reusing the first multimodal as text. The node tolerates either.
        inputs["text_notes"] = ctx.produced[mm_keys[0]]

    return [
        DagNodeSpec(
            node_id=step.name,
            impl="resolve_candidates",
            inputs=inputs,
            outputs={
                "candidates": ctx.emit(ArtifactRef("candidates", "candidates")),
                "all_notes": ctx.emit(ArtifactRef("notes", "all_notes")),
            },
            params=_step_params(step),
        )
    ]


def _lower_persist(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    if "all_notes" not in ctx.produced:
        raise ValueError(
            f"step '{step.name}': persist_canonical requires a consolidate step before it"
        )
    return [
        DagNodeSpec(
            node_id=step.name,
            impl="persist_notes",
            inputs={"all_notes": ctx.produced["all_notes"]},
            outputs={"coverage": ctx.emit(ArtifactRef("coverage", "coverage"))},
            params=_step_params(step),
        )
    ]


# Steps the recipe layer accepts but the current DAG executor does not
# yet implement (cross_link, write_articles, maintain). They are recorded
# in the compiled spec's ``params["deferred_steps"]`` so observability
# can report them, but no DAG node is emitted. When a real implementation
# lands, replace the entry below with a real lowering function.
def _defer(step: StepConfig, ctx: _CompileCtx) -> list[DagNodeSpec]:
    ctx.deferred_steps.append(
        {
            "step_name": step.name,
            "step_kind": step.kind,
            "model": step.model,
            "prompt": step.prompt,
            "schema": step.schema,
            "reason": "no DAG implementation yet — owned by the orchestrating agent",
        }
    )
    return []


_LOWER: dict[str, LowerFn] = {
    "profile_documents": _lower_profile,
    "identify_concepts": _lower_identify_concepts,
    "identify_people": _lower_identify_people,
    "identify_figures_tables": _lower_identify_multimodal,
    "consolidate": _lower_consolidate,
    "persist_canonical": _lower_persist,
    "cross_link": _defer,
    "write_articles": _defer,
    "maintain": _defer,
}


def compile_recipe(recipe: Recipe) -> DagRunSpec:
    """Compile a ``Recipe`` into an executable ``DagRunSpec``."""

    ctx = _CompileCtx(recipe)
    nodes: list[DagNodeSpec] = []
    for step in recipe.steps:
        try:
            lower = _LOWER[step.kind]
        except KeyError as exc:
            raise ValueError(
                f"step '{step.name}': no lowering registered for kind '{step.kind}'"
            ) from exc
        nodes.extend(lower(step, ctx))

    return DagRunSpec(
        workflow_id=f"recipe::{recipe.recipe_id}",
        nodes=tuple(nodes),
        strategy_id=recipe.recipe_id,
        config_hash=recipe.config_hash,
        config_source=recipe.source_path,
        params={
            "frontier": dict(recipe.frontier.__dict__),
            "description": recipe.description,
            "deferred_steps": ctx.deferred_steps,
        },
    )


__all__ = ["compile_recipe"]
