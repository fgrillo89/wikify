"""wikify validate ... — schema + structural checks for scratch artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from pydantic import ValidationError

from ..schema import WriteRequest, WriteResponse

app = typer.Typer(add_completion=False, help="Schema and structural validation for drafts.")

VALIDATION_SCHEMA_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    return [
        {
            "path": "/".join(str(part) for part in err.get("loc", ())),
            "code": err.get("type", "validation_error"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]


def _quote_in_chunk_errors(draft: WriteRequest, response: WriteResponse) -> list[dict]:
    """Verify every used `[^eN]` marker maps to an evidence entry whose quote is in the body.

    Marker `eN` is 1-based into `draft.evidence_v2` (position-based, not label-based).
    """
    errors: list[dict] = []
    for marker in response.used_markers:
        idx = _marker_to_index(marker)
        if idx is None or idx < 0 or idx >= len(draft.evidence_v2):
            errors.append(
                {
                    "path": f"used_markers/{marker}",
                    "code": "unknown_marker",
                    "message": (
                        f"response uses marker {marker!r} with no matching evidence_v2 entry"
                    ),
                }
            )
            continue
        evidence = draft.evidence_v2[idx]
        quote = (evidence.quote or "").strip()
        if quote and quote not in response.body_markdown:
            errors.append(
                {
                    "path": f"evidence_v2/{idx}/quote",
                    "code": "quote_not_in_body",
                    "message": f"quote for {marker!r} not found in response body",
                }
            )
    return errors


def _marker_to_index(marker: str) -> int | None:
    """Convert an evidence marker label (e.g. "e1" or "[^e1]") to a 0-based index."""
    if not marker:
        return None
    stripped = marker.strip().lstrip("[").lstrip("^").lstrip("e")
    stripped = stripped.rstrip("]")
    try:
        return int(stripped) - 1
    except ValueError:
        return None


@app.command("write")
def cmd_validate_write(
    draft: Path = typer.Option(..., "--draft", help="Path to draft-<page_id>.json."),
    response: Path = typer.Option(..., "--response", help="Path to response-<page_id>.json."),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Validation verdict output path. Defaults to validation-<page_id>.json next to draft.",
    ),
) -> None:
    """Validate a WriteResponse scratch payload against its WriteRequest draft."""
    draft_data = json.loads(draft.read_text(encoding="utf-8"))
    response_data = json.loads(response.read_text(encoding="utf-8"))

    errors: list[dict] = []
    structural_checks = {"pydantic": "pending", "quote_in_chunk": "pending"}

    try:
        parsed_draft = WriteRequest.model_validate(draft_data)
    except ValidationError as exc:
        errors.extend({**e, "path": f"draft/{e['path']}"} for e in _pydantic_errors(exc))
        structural_checks["pydantic"] = "fail"
        parsed_draft = None
    else:
        structural_checks["pydantic"] = "pass"

    try:
        parsed_response = WriteResponse.model_validate(response_data)
    except ValidationError as exc:
        errors.extend({**e, "path": f"response/{e['path']}"} for e in _pydantic_errors(exc))
        structural_checks["pydantic"] = "fail"
        parsed_response = None

    if parsed_draft is not None and parsed_response is not None:
        if parsed_draft.page_id != parsed_response.page_id:
            errors.append(
                {
                    "path": "page_id",
                    "code": "page_id_mismatch",
                    "message": (
                        f"draft.page_id={parsed_draft.page_id!r} "
                        f"!= response.page_id={parsed_response.page_id!r}"
                    ),
                }
            )
        quote_errors = _quote_in_chunk_errors(parsed_draft, parsed_response)
        structural_checks["quote_in_chunk"] = "fail" if quote_errors else "pass"
        errors.extend(quote_errors)

    ok = not errors
    page_id = (
        parsed_response.page_id if parsed_response else response_data.get("page_id", "unknown")
    )

    verdict = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "ok": ok,
        "page_id": page_id,
        "response_path": str(response),
        "errors": errors,
        "structural_checks": structural_checks,
        "checked_at": _utcnow(),
    }

    out_path = out or (draft.parent / f"validation-{page_id}.json")
    out_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    typer.echo(json.dumps({"ok": ok, "validation_path": str(out_path), "errors": len(errors)}))
    raise typer.Exit(code=0 if ok else 1)


__all__ = ["app"]
