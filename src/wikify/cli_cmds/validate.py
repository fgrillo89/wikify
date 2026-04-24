"""wikify validate ... — schema + structural checks for scratch artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from pydantic import ValidationError

from ..schema import WriteRequest, WriteResponse
from ..session import (
    SessionLockHeldError,
    apply_merge_patch,
    load_session,
    save_session,
    session_lock,
    touch,
)

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


_PROSE_MARKER_RE = re.compile(r"\[\^e(\d+)\]")


def _parse_prose_markers(body_markdown: str) -> set[int]:
    """Return 0-based indices of every `[^eN]` marker found anywhere in the body."""
    return {int(m.group(1)) - 1 for m in _PROSE_MARKER_RE.finditer(body_markdown)}


def _quote_grounding_errors(draft: WriteRequest, response: WriteResponse) -> list[dict]:
    """Verify every `[^eN]:` definition in the body is grounded in chunk_text.

    The ground-truth quote lives in the response body's References block.
    The subagent picks a verbatim substring from chunk_text and writes it
    into a `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line per
    reference/citation-format.md.

    This function treats the body definitions as the source of truth —
    not `response.used_markers`, which the subagent could silently empty
    while still writing valid-looking prose. `used_markers` is
    additionally cross-checked against the definition set so a mismatch
    is also surfaced.

    Grounding is verified by:

    1. Every in-prose `[^eN]` marker has a matching `[^eN]:` definition
       (enforced by `WriteResponse` validator, re-checked here so an
       empty `used_markers` does not reduce this function to a no-op).
    2. Every definition resolves to a 1-based position in draft.evidence_v2.
    3. The evidence entry carries non-empty chunk_text.
    4. The extracted definition-quote is a verbatim substring of chunk_text.
    5. `used_markers` equals the set of prose markers.

    A draft-side `evidence_v2[i].quote`, when non-empty, is treated as
    advisory only and is not cross-checked here.
    """
    body_quotes = _parse_ref_quotes(response.body_markdown)
    prose_markers = _parse_prose_markers(response.body_markdown)
    declared_markers = {
        idx for m in response.used_markers if (idx := _marker_to_index(m)) is not None
    }
    errors: list[dict] = []

    # 5. Cross-check used_markers bookkeeping against prose. Each side can
    # drift independently; either drift masks grounding failures.
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

    # 1. If the body uses no [^eN] markers at all, fail explicitly — the
    # WriteResponse validator normally catches this, but we re-check so
    # grounding logic is never vacuously satisfied.
    if not prose_markers and not declared_markers:
        errors.append(
            {
                "path": "body_markdown",
                "code": "no_markers",
                "message": "response body has no [^eN] markers; grounding cannot be verified",
            }
        )
        return errors

    # 2–4. Ground every marker that appears either in prose or in the
    # declared set. We take the union so that either form of drift still
    # gets every citation checked.
    checked = prose_markers | declared_markers
    for idx in sorted(checked):
        marker = f"e{idx + 1}"
        if idx < 0 or idx >= len(draft.evidence_v2):
            errors.append(
                {
                    "path": f"markers/{marker}",
                    "code": "unknown_marker",
                    "message": f"marker {marker!r} has no matching evidence_v2 entry",
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
    session_path: Path | None = typer.Option(
        None,
        "--session",
        help=(
            "Optional session.json path. When supplied AND validation ok=true, "
            "the session page entry for this page_id is transitioned to "
            "status=validated and validation_path is set under the session lock. "
            "This closes the atoms.md contract — bundle commit-page then refuses "
            "to promote pages whose status is not `validated`."
        ),
    ),
    owner: str | None = typer.Option(None, "--owner", help="Override the lock owner string."),
) -> None:
    """Validate a WriteResponse scratch payload against its WriteRequest draft."""
    draft_data = json.loads(draft.read_text(encoding="utf-8"))
    response_data = json.loads(response.read_text(encoding="utf-8"))
    # `schema_version` is a scratch-envelope field, not part of the canonical
    # WriteRequest / WriteResponse Pydantic models (which are `extra="forbid"`).
    # Strip from both before validation so either side can carry the envelope
    # without tripping model validation.
    draft_data_clean = {k: v for k, v in draft_data.items() if k != "schema_version"}
    response_data_clean = {k: v for k, v in response_data.items() if k != "schema_version"}

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
        parsed_response = WriteResponse.model_validate(response_data_clean)
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
        body_errors = [
            e
            for e in quote_errors
            if e["code"]
            in {
                "quote_not_in_body",
                "no_markers",
                "undeclared_prose_marker",
                "spurious_used_marker",
            }
        ]
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

    # Bind the verdict to the specific response bytes it witnessed. A
    # commit-page caller can then refuse any verdict whose bytes no
    # longer match the response on disk (stale/edited response).
    response_bytes = response.read_bytes()
    verdict["response_sha256"] = hashlib.sha256(response_bytes).hexdigest()
    verdict["response_path"] = str(response.resolve())

    out_path = out or (draft.parent / f"validation-{page_id}.json")
    out_path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")

    session_patched = False
    if ok and session_path is not None:
        try:
            with session_lock(
                session_path,
                owner=owner or f"wikify-cli/pid-{os.getpid()}",
            ):
                fresh = load_session(session_path)
                new_pages = [p.model_dump(mode="json") for p in fresh.pages]
                found = False
                for entry in new_pages:
                    if entry["page_id"] == page_id:
                        entry["status"] = "validated"
                        entry["validation_path"] = str(out_path)
                        found = True
                        break
                if not found:
                    new_pages.append(
                        {
                            "page_id": page_id,
                            "status": "validated",
                            "draft_path": None,
                            "validation_path": str(out_path),
                        }
                    )
                updated = apply_merge_patch(fresh, {"pages": new_pages})
                save_session(session_path, touch(updated))
                session_patched = True
        except SessionLockHeldError as exc:
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "lock_held",
                        "owner": exc.owner,
                        "acquired_at": exc.acquired_at,
                    }
                ),
                err=True,
            )
            raise typer.Exit(code=2) from exc

    typer.echo(
        json.dumps(
            {
                "ok": ok,
                "validation_path": str(out_path),
                "errors": len(errors),
                "session_patched": session_patched,
            }
        )
    )
    raise typer.Exit(code=0 if ok else 1)


__all__ = ["app"]
