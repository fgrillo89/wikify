"""Prompt registry tests (item 7a)."""

import pytest

from wikify_simple.prompts import Prompt, load_prompt
from wikify_simple.prompts.registry import all_prompts


def test_all_three_prompts_load():
    names = {
        "wikify_simple/extract",
        "wikify_simple/write",
        "wikify_simple/query",
    }
    registered = set(all_prompts().keys())
    assert names <= registered


@pytest.mark.parametrize(
    "name",
    [
        "wikify_simple/extract",
        "wikify_simple/write",
        "wikify_simple/query",
    ],
)
def test_prompt_has_nonempty_template(name):
    p = load_prompt(name)
    assert isinstance(p, Prompt)
    assert p.name == name
    assert p.prompt_template.strip()
    assert p.role in {"extractor", "writer", "querier"}


def test_missing_prompt_raises():
    with pytest.raises(KeyError):
        load_prompt("wikify_simple/does_not_exist/v1")


def test_registry_frozen():
    p = load_prompt("wikify_simple/extract")
    # frozen dataclass: assignment forbidden
    with pytest.raises(Exception):
        p.name = "mutated"  # type: ignore[misc]
