"""Tests for wikify.papers.agent.tool_schema."""

from __future__ import annotations

from typing import Optional

from wikify.papers.agent.tool_schema import fn_to_tool_schema, fns_to_tool_schemas

# ---------------------------------------------------------------------------
# Helper functions used as test fixtures
# ---------------------------------------------------------------------------


def fn_str_param(query: str) -> list:
    """Search for papers."""
    return []


def fn_int_default(query: str, top_k: int = 10) -> list:
    """Search with a limit."""
    return []


def fn_optional_param(query: str, field: Optional[str] = None) -> list:
    """Search with optional field."""
    return []


def fn_optional_no_default(query: str, field: Optional[str]) -> list:
    """Search with optional field, no default."""
    return []


def fn_no_type_hints(query, top_k=5) -> None:
    """Function without type hints."""


def fn_no_docstring(query: str) -> None:
    pass


def fn_google_docstring(query: str, top_k: int = 10, field: Optional[str] = None) -> list:
    """Search for papers matching a query.

    Args:
        query: The search query string.
        top_k: Maximum number of results to return.
        field: Optional field to restrict the search.

    Returns:
        List of matching papers.
    """
    return []


def fn_google_multiline(query: str) -> list:
    """Search papers.

    Args:
        query: The search query string, which can be
            arbitrarily long and multi-line.

    Returns:
        List of results.
    """
    return []


def fn_list_param(tags: list[str]) -> None:
    """Function with a list parameter."""


def fn_bare_list(items: list) -> None:
    """Function with bare list."""


# Union pipe syntax (Python 3.10+)
def fn_union_pipe(query: str, limit: int | None = None) -> list:
    """Search with pipe union syntax."""
    return []


# Realistic ScholarForge-style function
def list_papers(
    topic: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    include_abstract: bool = False,
) -> list:
    """List papers in the corpus, optionally filtered by topic.

    Args:
        topic: Topic to filter by. If None, returns all papers.
        limit: Maximum number of papers to return.
        offset: Number of papers to skip for pagination.
        include_abstract: Whether to include abstracts in results.

    Returns:
        List of paper records.
    """
    return []


# ---------------------------------------------------------------------------
# Tests: basic type conversion
# ---------------------------------------------------------------------------


def test_str_param_produces_string_type():
    schema = fn_to_tool_schema(fn_str_param)
    props = schema["function"]["parameters"]["properties"]
    assert props["query"]["type"] == "string"


def test_str_param_is_required():
    schema = fn_to_tool_schema(fn_str_param)
    assert "query" in schema["function"]["parameters"]["required"]


def test_top_level_schema_shape():
    schema = fn_to_tool_schema(fn_str_param)
    assert schema["type"] == "function"
    assert "function" in schema
    assert schema["function"]["name"] == "fn_str_param"
    assert "parameters" in schema["function"]
    assert schema["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Tests: defaults
# ---------------------------------------------------------------------------


def test_int_with_default_not_required():
    schema = fn_to_tool_schema(fn_int_default)
    required = schema["function"]["parameters"]["required"]
    assert "top_k" not in required


def test_int_with_default_has_default_value():
    schema = fn_to_tool_schema(fn_int_default)
    prop = schema["function"]["parameters"]["properties"]["top_k"]
    assert prop["default"] == 10
    assert prop["type"] == "integer"


def test_required_param_with_default_omitted():
    schema = fn_to_tool_schema(fn_int_default)
    assert "query" in schema["function"]["parameters"]["required"]


# ---------------------------------------------------------------------------
# Tests: Optional / nullable
# ---------------------------------------------------------------------------


def test_optional_param_with_none_default_not_required():
    schema = fn_to_tool_schema(fn_optional_param)
    required = schema["function"]["parameters"]["required"]
    assert "field" not in required


def test_optional_param_no_default_not_required():
    """Optional[T] without a default should still be not required."""
    schema = fn_to_tool_schema(fn_optional_no_default)
    required = schema["function"]["parameters"]["required"]
    assert "field" not in required


def test_optional_inner_type_is_string():
    schema = fn_to_tool_schema(fn_optional_param)
    prop = schema["function"]["parameters"]["properties"]["field"]
    assert prop["type"] == "string"


# ---------------------------------------------------------------------------
# Tests: no type hints
# ---------------------------------------------------------------------------


def test_no_type_hints_defaults_to_string():
    schema = fn_to_tool_schema(fn_no_type_hints)
    props = schema["function"]["parameters"]["properties"]
    assert props["query"]["type"] == "string"


def test_no_type_hints_default_value_still_captured():
    schema = fn_to_tool_schema(fn_no_type_hints)
    prop = schema["function"]["parameters"]["properties"]["top_k"]
    assert prop["default"] == 5


# ---------------------------------------------------------------------------
# Tests: docstring
# ---------------------------------------------------------------------------


def test_no_docstring_description_is_empty():
    schema = fn_to_tool_schema(fn_no_docstring)
    assert schema["function"]["description"] == ""


def test_description_is_first_line_of_docstring():
    schema = fn_to_tool_schema(fn_str_param)
    assert schema["function"]["description"] == "Search for papers."


# ---------------------------------------------------------------------------
# Tests: Google-style Args section
# ---------------------------------------------------------------------------


def test_google_docstring_param_description_extracted():
    schema = fn_to_tool_schema(fn_google_docstring)
    props = schema["function"]["parameters"]["properties"]
    assert props["query"]["description"] == "The search query string."
    assert props["top_k"]["description"] == "Maximum number of results to return."
    assert props["field"]["description"] == "Optional field to restrict the search."


def test_google_docstring_description_is_first_line():
    schema = fn_to_tool_schema(fn_google_docstring)
    assert schema["function"]["description"] == "Search for papers matching a query."


def test_google_multiline_arg_description():
    schema = fn_to_tool_schema(fn_google_multiline)
    desc = schema["function"]["parameters"]["properties"]["query"]["description"]
    assert "arbitrarily long" in desc


# ---------------------------------------------------------------------------
# Tests: list types
# ---------------------------------------------------------------------------


def test_list_of_str_produces_array_with_items():
    schema = fn_to_tool_schema(fn_list_param)
    prop = schema["function"]["parameters"]["properties"]["tags"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_bare_list_produces_array():
    schema = fn_to_tool_schema(fn_bare_list)
    prop = schema["function"]["parameters"]["properties"]["items"]
    assert prop["type"] == "array"
    assert "items" not in prop


# ---------------------------------------------------------------------------
# Tests: pipe union syntax (Python 3.10+)
# ---------------------------------------------------------------------------


def test_pipe_union_optional_not_required():
    schema = fn_to_tool_schema(fn_union_pipe)
    required = schema["function"]["parameters"]["required"]
    assert "limit" not in required


def test_pipe_union_inner_type_is_integer():
    schema = fn_to_tool_schema(fn_union_pipe)
    prop = schema["function"]["parameters"]["properties"]["limit"]
    assert prop["type"] == "integer"


# ---------------------------------------------------------------------------
# Tests: fns_to_tool_schemas
# ---------------------------------------------------------------------------


def test_fns_to_tool_schemas_returns_correct_length():
    schemas = fns_to_tool_schemas([fn_str_param, fn_int_default, fn_optional_param])
    assert len(schemas) == 3


def test_fns_to_tool_schemas_each_is_valid():
    schemas = fns_to_tool_schemas([fn_str_param, fn_int_default])
    for schema in schemas:
        assert schema["type"] == "function"
        assert "function" in schema


# ---------------------------------------------------------------------------
# Tests: realistic ScholarForge function (list_papers)
# ---------------------------------------------------------------------------


def test_list_papers_schema_name():
    schema = fn_to_tool_schema(list_papers)
    assert schema["function"]["name"] == "list_papers"


def test_list_papers_no_required_params():
    schema = fn_to_tool_schema(list_papers)
    # All params have defaults, so required should be empty
    assert schema["function"]["parameters"]["required"] == []


def test_list_papers_limit_is_integer_with_default():
    schema = fn_to_tool_schema(list_papers)
    prop = schema["function"]["parameters"]["properties"]["limit"]
    assert prop["type"] == "integer"
    assert prop["default"] == 20


def test_list_papers_include_abstract_is_boolean():
    schema = fn_to_tool_schema(list_papers)
    prop = schema["function"]["parameters"]["properties"]["include_abstract"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False


def test_list_papers_topic_description_extracted():
    schema = fn_to_tool_schema(list_papers)
    prop = schema["function"]["parameters"]["properties"]["topic"]
    assert "description" in prop
    assert "None" in prop["description"] or "filter" in prop["description"]


def test_list_papers_description_is_first_line():
    schema = fn_to_tool_schema(list_papers)
    assert "corpus" in schema["function"]["description"]
