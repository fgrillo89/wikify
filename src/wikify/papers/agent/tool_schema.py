"""Convert plain Python functions into litellm-compatible tool schemas via introspection."""

from __future__ import annotations

import inspect
import re
import sys
import types
import typing
from collections.abc import Callable
from typing import Union, get_args, get_origin

__all__ = ["fn_to_tool_schema", "fns_to_tool_schemas"]

# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_SCALAR_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _is_optional(hint: object) -> tuple[bool, object]:
    """Return (is_optional, inner_type).

    Handles both ``Optional[T]`` (typing.Union) and ``T | None`` (types.UnionType
    available in Python 3.10+).
    """
    origin = get_origin(hint)

    # typing.Optional[T] / typing.Union[T, None]
    if origin is Union:
        args = get_args(hint)
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args:
            inner = non_none[0] if len(non_none) == 1 else hint
            return True, inner

    # T | None  (Python 3.10+ types.UnionType)
    if sys.version_info >= (3, 10) and isinstance(hint, types.UnionType):
        args = get_args(hint)
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args:
            inner = non_none[0] if len(non_none) == 1 else hint
            return True, inner

    return False, hint


def _hint_to_json_schema(hint: object) -> dict:
    """Convert a single Python type hint to a JSON Schema fragment."""
    if hint is inspect.Parameter.empty or hint is None:
        return {"type": "string"}

    # Unwrap Optional/union-with-None (the optional flag is handled elsewhere)
    is_opt, inner = _is_optional(hint)
    if is_opt:
        hint = inner

    # Scalar types
    if hint in _SCALAR_MAP:
        return {"type": _SCALAR_MAP[hint]}

    # list[T] or list
    origin = get_origin(hint)
    if origin is list or hint is list:
        args = get_args(hint)
        if args:
            items = _hint_to_json_schema(args[0])
            return {"type": "array", "items": items}
        return {"type": "array"}

    # Fallback
    return {"type": "string"}


# ---------------------------------------------------------------------------
# Docstring parsing
# ---------------------------------------------------------------------------

_ARGS_RE = re.compile(r"^\s{4,}(\w+)(?:\s*\([^)]*\))?\s*:\s*(.+)$")
_CONTINUATION_RE = re.compile(r"^\s{8,}(\S.*)$")


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (first_line_description, {param_name: description}) from docstring."""
    if not doc:
        return "", {}

    lines = doc.splitlines()

    # First non-empty line is the description
    description = ""
    for line in lines:
        stripped = line.strip()
        if stripped:
            description = stripped
            break

    # Find Google-style Args: section
    param_descs: dict[str, str] = {}
    in_args = False
    current_param: str | None = None

    for line in lines:
        if re.match(r"^\s*Args\s*:", line):
            in_args = True
            current_param = None
            continue

        if in_args:
            # Detect another section header (non-indented or top-level keyword)
            if re.match(r"^\s*\w[\w\s]*\s*:", line) and not _ARGS_RE.match(line):
                # Only break if it looks like a section header (e.g. "Returns:")
                if re.match(r"^\s{0,4}\w[\w\s]*\s*:", line) and not _ARGS_RE.match(line):
                    in_args = False
                    current_param = None
                    continue

            m = _ARGS_RE.match(line)
            if m:
                current_param = m.group(1)
                param_descs[current_param] = m.group(2).strip()
                continue

            # Continuation line
            if current_param is not None:
                cont = _CONTINUATION_RE.match(line)
                if cont:
                    param_descs[current_param] = (
                        param_descs[current_param] + " " + cont.group(1).strip()
                    )
                    continue

            # Blank line ends continuation but not the section
            if not line.strip():
                current_param = None

    return description, param_descs


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------


def fn_to_tool_schema(fn: Callable) -> dict:
    """Convert a function's signature + docstring into an OpenAI-style tool schema.

    Uses inspect.signature for parameter names, types, and defaults and the
    function's docstring for descriptions.

    Supports: str, int, float, bool, Optional[T], T | None, list[T].

    Returns a dict matching litellm's expected format::

        {
            "type": "function",
            "function": {
                "name": "search_papers",
                "description": "First line of docstring...",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "..."},
                        "top_k": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
        }
    """
    sig = inspect.signature(fn)

    # Best-effort type hints; fall back gracefully
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {
            name: param.annotation
            for name, param in sig.parameters.items()
            if param.annotation is not inspect.Parameter.empty
        }

    description, param_descs = _parse_docstring(inspect.getdoc(fn) or "")

    properties: dict[str, dict] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        # Skip self/cls for methods
        if name in ("self", "cls"):
            continue

        hint = hints.get(name, inspect.Parameter.empty)
        is_opt, _inner = _is_optional(hint)
        has_default = param.default is not inspect.Parameter.empty

        prop: dict = _hint_to_json_schema(hint)

        # Per-parameter description from docstring
        if name in param_descs:
            prop["description"] = param_descs[name]

        if has_default:
            prop["default"] = param.default

        properties[name] = prop

        # Required: no default AND not optional/nullable
        if not has_default and not is_opt:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def fns_to_tool_schemas(fns: list[Callable]) -> list[dict]:
    """Convert multiple functions to tool schemas."""
    return [fn_to_tool_schema(fn) for fn in fns]
