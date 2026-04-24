"""wikify validate ... — schema + structural checks for scratch artifacts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from pydantic import ValidationError

from ..schema import WriteRequest, WriteResponse

app = typer.Typer(add_completion=False, help="Schema and structural validation for drafts.")

VALIDATION_SCHEMA_VERSION = 1

# Match `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` in a References block.
_REF_DEF_RE = re.compile(
    r'^\[\^e(\d+)\]:\s*(?P<body>.*?)\s*>\s*"(?P<quote>.+?)"\s*$',
    re.MULTILINE,
)


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


def _parse_ref_quotes(body_markdown: str) -> dict[int, str]:
    """Extract `[^eN]: ... > "<quote>"` pairs from the response body."""
    quotes: dict[int, str] = {}
    for match in _REF_DEF_RE.finditer(body_markdown):
        idx = int(match.group(1)) - 1  # convert 1-based marker to 0-based
        quotes[idx] = match.group("quote")
    return quotes


def _quote_grounding_errors(draft: WriteRequest, response: WriteResponse) -> list[dict]:
    """Verify every used `[^eN]` marker is grounded in its source chunk.

    The ground-truth quote lives in the response body's References block,
    not in the draft's evidence_v2 — the draft provides chunk_text for
    the subagent to pick a verbatim quote from, and the subagent writes
    the quote into a `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line.
    Grounding is verified by:

    1. Every used marker has a `[^eN]:` definition in the body.
    2. Every used marker maps to a 1-based position in draft.evidence_v2.
    3. The evidence entry carries non-empty chunk_text.
    4. The extracted body-quote is a verbatim substring of chunk_text.

    A draft-side `evidence_v2[i].quote`, when non-empty, is treated as an
    advisory suggestion and is not cross-checked here.
    """
    body_quotes = _parse_ref_quotes(response.body_markdown)
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
        body_quote = body_quotes.get(idx)
        if not body_quote:
            errors.append(
                {
                    "path": f"body_markdown/[^{marker}]",
                    "code": "quote_not_in_body",
                    "message": (
                        f"used marker {marker!r} has no matching `[^{marker}]:` "
                        "definition in the body References block"
                    ),
                }
            )
            continue
        evidence = draft.evidence_v2[idx]
        chunk_text = evidence.chunk_text or ""
        if not chunk_text:
            errors.append(
                {
                    "path": f"evidence_v2/{idx}/chunk_text",
                    "code": "chunk_text_missing",
                    "message": (
                        f"evidence_v2[{idx}] has no chunk_text; cannot verify source grounding"
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
                        f"evidence_v2[{idx}].chunk_text — fabricated or corrupted citation"
                    ),
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
    # `schema_version` is a scratch-envelope field, not part of WriteRequest.
    # Strip before Pydantic validation so the canonical model stays clean.
    draft_data_clean = {k: v for k, v in draft_data.items() if k != "schema_version"}

    errors: list[dict] = []
    structural_checks = {
        "pydantic": "pending",
        "quote_in_body": "pending",
        "quote_in_source": "pending",
    }

    try:
        parsed_draft = WriteRequest.model_validate(draft_data_clean)
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
        quote_errors = _quote_grounding_errors(parsed_draft, parsed_response)
        body_errors = [e for e in quote_errors if e["code"] == "quote_not_in_body"]
        source_errors = [
            e
            for e in quote_errors
            if e["code"] in {"quote_not_in_source", "chunk_text_missing", "unknown_marker"}
        ]
        structural_checks["quote_in_body"] = "fail" if body_errors else "pass"
        structural_checks["quote_in_source"] = "fail" if source_errors else "pass"
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
