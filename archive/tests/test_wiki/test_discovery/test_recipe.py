"""Recipe loader + compiler tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.wiki.discovery.contracts import ArtifactRef
from wikify.wiki.discovery.dag import validate_dag
from wikify.wiki.discovery.executor import DagExecutor
from wikify.wiki.discovery.recipe import RecipeError, load_recipe_yaml, parse_recipe
from wikify.wiki.discovery.recipe_compiler import compile_recipe
from wikify.wiki.discovery.registry import default_registry

DEFAULT_RECIPE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "wikify"
    / "wiki"
    / "recipes"
    / "default_publication.yaml"
)


def test_default_recipe_loads_and_compiles():
    recipe = load_recipe_yaml(DEFAULT_RECIPE)
    assert recipe.recipe_id == "default_publication"
    assert recipe.config_hash
    assert recipe.frontier.budget_per_epoch == 64
    assert {s.name for s in recipe.steps} >= {
        "profile_documents",
        "identify_concepts",
        "consolidate",
        "persist_canonical",
        "write_articles",
    }

    spec = compile_recipe(recipe)
    assert spec.workflow_id == "recipe::default_publication"
    assert spec.config_hash == recipe.config_hash
    # Compiled spec must validate as a DAG with the seed `document` artifact.
    seeds = {"document": ArtifactRef("document", "document")}
    order = validate_dag(spec, seed_artifacts=seeds)
    # Profile must come before identify_concepts.
    assert order.index("profile_documents") < order.index("identify_concepts__plan")


def test_compiled_recipe_executes_end_to_end():
    recipe = load_recipe_yaml(DEFAULT_RECIPE)
    spec = compile_recipe(recipe)
    executor = DagExecutor(default_registry())
    result = executor.run(
        spec,
        seed_artifacts={
            "document": (
                ArtifactRef("document", "document"),
                {
                    "id": "doc-1",
                    "type": "publication",
                    "sections": ["abstract"],
                    "modalities": ["text", "image", "table"],
                    "chunks": [{"id": "c1", "text": "alpha", "section": "abstract"}],
                    "figures": [{"id": "f1", "caption": "fig", "image_path": "/x.png"}],
                    "tables": [{"id": "t1", "caption": "tbl", "rows": []}],
                },
            )
        },
    )
    assert result.workflow_id == "recipe::default_publication"
    assert result.strategy_id == "default_publication"
    assert {t.node_id for t in result.timings} >= {
        "profile_documents",
        "identify_concepts__plan",
        "identify_concepts",
        "consolidate",
        "persist_canonical",
    }


def test_recipe_rejects_unknown_step_kind():
    with pytest.raises(RecipeError, match="unknown kind"):
        parse_recipe(
            {
                "recipe_id": "bad",
                "steps": [{"name": "x", "kind": "ghost"}],
            }
        )


def test_recipe_rejects_missing_recipe_id():
    with pytest.raises(RecipeError, match="recipe_id"):
        parse_recipe({"steps": [{"name": "p", "kind": "profile_documents"}]})


def test_recipe_step_name_defaults_to_kind():
    recipe = parse_recipe(
        {
            "recipe_id": "x",
            "steps": [
                {"kind": "profile_documents"},
                {"kind": "identify_concepts"},
            ],
        }
    )
    assert [s.name for s in recipe.steps] == ["profile_documents", "identify_concepts"]


def test_recipe_rejects_unknown_frontier_priority():
    with pytest.raises(RecipeError, match="frontier.priority"):
        parse_recipe(
            {
                "recipe_id": "x",
                "frontier": {"priority": "mystery"},
                "steps": [{"kind": "profile_documents"}],
            }
        )


def test_recipe_rejects_dangling_inputs_from():
    with pytest.raises(RecipeError, match="unknown step"):
        parse_recipe(
            {
                "recipe_id": "x",
                "steps": [
                    {"name": "p", "kind": "profile_documents"},
                    {"name": "c", "kind": "consolidate", "inputs_from": ["ghost"]},
                ],
            }
        )


def test_recipe_rejects_missing_prompt_file():
    with pytest.raises(RecipeError, match="steps\\[p\\]: prompt file"):
        parse_recipe(
            {
                "recipe_id": "x",
                "steps": [
                    {
                        "name": "p",
                        "kind": "profile_documents",
                        "prompt": "src/wikify/wiki/prompts/does_not_exist.md",
                    }
                ],
            }
        )


def test_recipe_rejects_missing_schema_file():
    with pytest.raises(RecipeError, match="steps\\[c\\]: schema file"):
        parse_recipe(
            {
                "recipe_id": "x",
                "steps": [
                    {
                        "name": "c",
                        "kind": "identify_concepts",
                        "schema": "src/wikify/wiki/schemas/does_not_exist.json",
                    }
                ],
            }
        )


def test_recipe_rejects_missing_style_guide_file():
    with pytest.raises(RecipeError, match="steps\\[w\\]: style_guide file"):
        parse_recipe(
            {
                "recipe_id": "x",
                "steps": [
                    {
                        "name": "w",
                        "kind": "write_articles",
                        "style_guide": "src/wikify/wiki/prompts/missing_style.md",
                    }
                ],
            }
        )


def test_consolidate_requires_upstream_notes():
    recipe = parse_recipe(
        {
            "recipe_id": "x",
            "steps": [
                {"name": "p", "kind": "profile_documents"},
                {"name": "c", "kind": "consolidate"},
            ],
        }
    )
    with pytest.raises(ValueError, match="no upstream notes"):
        compile_recipe(recipe)
