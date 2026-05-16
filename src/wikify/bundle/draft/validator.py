"""Validator — schema + structural + quote-grounding checks for response.json.

Owns the validation logic: schema checks, structural
checks (``_check_wikipedia_structure`` / ``_check_figure_mentions``),
and verbatim quote-grounding. Reads ``draft.json`` + ``response.json``
from the concept folder; writes ``validation.json``.

The verdict has the shape::

    {
      "schema_version": 1,
      "ok": bool,
      "page_id": str,
      "response_path": str,
      "draft_path": str,
      "errors": [{"path": str, "code": str, "message": str}],
      "structural_checks": {<check>: <bool>},
      "checked_at": ISO8601,
    }
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import ValidationError

from ...api import Bundle
from .artifact import (
    draft_path,
    read_json,
    response_path,
    validation_path,
    write_json,
)
from .schema import (
    QuoteNotInChunkError,
    WriteRequest,
    WriteResponse,
    _check_figure_mentions,
    _check_wikipedia_structure,
)

VALIDATION_SCHEMA_VERSION = 1


_REF_DEF_RE = re.compile(
    r'^\[\^e(\d+)\]:\s*(?P<body>.*?)\s*>\s*"(?P<quote>.+?)"\s*$',
    re.MULTILINE,
)
_PROSE_MARKER_RE = re.compile(r"\[\^e(\d+)\]")
_FIGURE_PLACEHOLDER_RE = re.compile(r"\{\{figure:([A-Za-z0-9_.-]+)\}\}")


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_envelope(data: dict) -> dict:
    """Drop the ``schema_version``/``task`` scratch envelope keys."""
    out = {k: v for k, v in data.items() if k not in {"schema_version", "task"}}
    return out


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    return [
        {
            "path": "/".join(str(part) for part in err.get("loc", ())),
            "code": err.get("type", "validation_error"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]


def _parse_ref_quotes(body: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for m in _REF_DEF_RE.finditer(body):
        out[int(m.group(1)) - 1] = m.group("quote")
    return out


def _parse_prose_markers(body: str) -> set[int]:
    return {int(m.group(1)) - 1 for m in _PROSE_MARKER_RE.finditer(body)}


def _marker_to_index(marker: str) -> int | None:
    if not marker:
        return None
    s = marker.strip().lstrip("[").lstrip("^").lstrip("e").rstrip("]")
    try:
        return int(s) - 1
    except ValueError:
        return None


def _quote_grounding_errors(
    draft: WriteRequest, response: WriteResponse
) -> list[dict]:
    """Verify every ``[^eN]:`` definition in the body is grounded in
    ``evidence[i].chunk_text``.

    Each marker resolves 1:1 to a ``[^eN]:`` definition, the body quote
    must be a verbatim substring of the evidence chunk's source text,
    and ``used_markers`` must match the prose markers exactly.
    """
    body_quotes = _parse_ref_quotes(response.body_markdown)
    prose_markers = _parse_prose_markers(response.body_markdown)
    declared_markers = {
        idx
        for m in response.used_markers
        if (idx := _marker_to_index(m)) is not None
    }
    errors: list[dict] = []

    undeclared = sorted(prose_markers - declared_markers)
    if undeclared:
        errors.append(
            {
                "path": "used_markers",
                "code": "undeclared_prose_marker",
                "message": (
                    f"body uses marker(s) {sorted(f'e{i + 1}' for i in undeclared)} "
                    "that are missing from used_markers"
                ),
            }
        )
    spurious = sorted(declared_markers - prose_markers)
    if spurious:
        errors.append(
            {
                "path": "used_markers",
                "code": "spurious_used_marker",
                "message": (
                    f"used_markers contains {sorted(f'e{i + 1}' for i in spurious)} "
                    "with no corresponding `[^eN]` in prose"
                ),
            }
        )

    if not prose_markers and not declared_markers:
        errors.append(
            {
                "path": "body_markdown",
                "code": "no_markers",
                "message": "response body has no [^eN] markers; grounding cannot be verified",
            }
        )
        return errors

    checked = prose_markers | declared_markers
    for idx in sorted(checked):
        marker = f"e{idx + 1}"
        if idx < 0 or idx >= len(draft.evidence):
            errors.append(
                {
                    "path": f"markers/{marker}",
                    "code": "unknown_marker",
                    "message": f"marker {marker!r} has no matching evidence entry",
                }
            )
            continue
        body_quote = body_quotes.get(idx)
        if not body_quote:
            errors.append(
                {
                    "path": f"body_markdown/[^{marker}]",
                    "code": "quote_not_in_body",
                    "message": (
                        f"marker {marker!r} has no `[^{marker}]:` definition "
                        "in the body References block"
                    ),
                }
            )
            continue
        evidence = draft.evidence[idx]
        chunk_text = evidence.chunk_text or ""
        if not chunk_text:
            errors.append(
                {
                    "path": f"evidence/{idx}/chunk_text",
                    "code": "chunk_text_missing",
                    "message": (
                        f"evidence[{idx}] has no chunk_text; cannot verify source grounding"
                    ),
                }
            )
            continue
        if body_quote not in chunk_text:
            errors.append(
                {
                    "path": f"body_markdown/[^{marker}]",
                    "code": "quote_not_in_source",
                    "message": (
                        f"body quote for {marker!r} is not a substring of "
                        f"evidence[{idx}].chunk_text — fabricated or corrupted citation"
                    ),
                }
            )
    return errors


def _figure_selection_errors(
    draft: WriteRequest, response: WriteResponse
) -> list[dict]:
    request_by_id = {fig.id: fig for fig in draft.figures}
    placeholders = _FIGURE_PLACEHOLDER_RE.findall(response.body_markdown)
    placeholder_set = set(placeholders)
    selected_by_anchor = {fig.placement_anchor: fig for fig in response.figures}
    errors: list[dict] = []

    if len(selected_by_anchor) != len(response.figures):
        errors.append(
            {
                "path": "figures",
                "code": "duplicate_figure_anchor",
                "message": "figures must use unique placement_anchor values",
            }
        )

    for anchor in sorted(placeholder_set - set(selected_by_anchor)):
        errors.append(
            {
                "path": "body_markdown",
                "code": "unknown_figure_placeholder",
                "message": f"figure placeholder {anchor!r} has no matching figures entry",
            }
        )

    for fig in response.figures:
        if fig.placement_anchor not in placeholder_set:
            errors.append(
                {
                    "path": "figures",
                    "code": "unused_selected_figure",
                    "message": (
                        f"selected figure {fig.figure_id!r} has no "
                        f"{{{{figure:{fig.placement_anchor}}}}} placeholder"
                    ),
                }
            )
        allowed = request_by_id.get(fig.figure_id)
        if allowed is None:
            errors.append(
                {
                    "path": "figures",
                    "code": "unknown_figure_id",
                    "message": (
                        f"selected figure {fig.figure_id!r} is not in "
                        "the draft figures list"
                    ),
                }
            )
            continue
        if fig.path.replace("\\", "/") != allowed.path.replace("\\", "/"):
            errors.append(
                {
                    "path": "figures",
                    "code": "figure_path_mismatch",
                    "message": (
                        f"selected figure {fig.figure_id!r} path does not match "
                        "the draft figure candidate"
                    ),
                }
            )
        if fig.source_marker and fig.source_marker not in response.used_markers:
            errors.append(
                {
                    "path": "figures",
                    "code": "unknown_figure_source_marker",
                    "message": (
                        f"selected figure {fig.figure_id!r} cites source_marker "
                        f"{fig.source_marker!r}, which is not in used_markers"
                    ),
                }
            )
    return errors


def validate_response_data(draft_data: dict, response_data: dict) -> dict:
    """Run every check on raw draft + response dicts. Does not touch disk.

    Used by ``wikify draft check --dry-run`` so a writer subagent can
    pre-validate a response candidate before committing it to disk.
    Returns the verdict dict in the same shape as ``validate_response``.
    """
    draft_data = _strip_envelope(draft_data)
    response_data = _strip_envelope(response_data)
    return _run_checks(draft_data, response_data, draft_p="", response_p="")


def validate_response(bundle: Bundle, slug: str) -> dict:
    """Run every check on draft.json + response.json and write
    validation.json. Returns the verdict dict.
    """
    draft_p = draft_path(bundle, slug)
    response_p = response_path(bundle, slug)

    draft_data = _strip_envelope(read_json(draft_p))
    response_data = _strip_envelope(read_json(response_p))

    verdict = _run_checks(
        draft_data, response_data,
        draft_p=str(draft_p), response_p=str(response_p),
    )
    write_json(validation_path(bundle, slug), verdict)
    return verdict


def _run_checks(
    draft_data: dict,
    response_data: dict,
    *,
    draft_p: str,
    response_p: str,
) -> dict:
    errors: list[dict] = []
    structural: dict[str, bool] = {}

    # --- WriteRequest ----------------------------------------------------
    try:
        draft = WriteRequest.model_validate(draft_data)
        structural["draft_schema"] = True
    except ValidationError as exc:
        errors.extend(_pydantic_errors(exc))
        structural["draft_schema"] = False
        draft = None

    # --- WriteResponse ---------------------------------------------------
    try:
        response = WriteResponse.model_validate(response_data)
        structural["response_schema"] = True
    except ValidationError as exc:
        errors.extend(_pydantic_errors(exc))
        structural["response_schema"] = False
        response = None

    page_id = ""
    if draft is not None:
        page_id = draft.page_id
    elif response is not None:
        page_id = response.page_id

    # --- Structural checks ---------------------------------------------
    if response is not None:
        try:
            _check_wikipedia_structure(response.body_markdown, page_kind=response.page_kind)
            structural["wikipedia_structure"] = True
        except (ValueError, ValidationError) as exc:
            structural["wikipedia_structure"] = False
            errors.append(
                {
                    "path": "body_markdown",
                    "code": "wikipedia_structure",
                    "message": str(exc),
                }
            )
        try:
            _check_figure_mentions(response.body_markdown)
            structural["figure_mentions"] = True
        except (ValueError, ValidationError) as exc:
            structural["figure_mentions"] = False
            errors.append(
                {
                    "path": "body_markdown",
                    "code": "figure_mentions",
                    "message": str(exc),
                }
            )

    # --- Quote grounding ------------------------------------------------
    if draft is not None and response is not None:
        try:
            grounding_errors = _quote_grounding_errors(draft, response)
        except QuoteNotInChunkError as exc:
            grounding_errors = [
                {
                    "path": "body_markdown",
                    "code": "quote_not_in_source",
                    "message": str(exc),
                }
            ]
        errors.extend(grounding_errors)
        structural["quote_grounding"] = not grounding_errors
        figure_errors = _figure_selection_errors(draft, response)
        errors.extend(figure_errors)
        structural["figure_selection"] = not figure_errors

    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "ok": len(errors) == 0,
        "page_id": page_id,
        "response_path": response_p,
        "draft_path": draft_p,
        "errors": errors,
        "structural_checks": structural,
        "checked_at": _utcnow(),
    }
