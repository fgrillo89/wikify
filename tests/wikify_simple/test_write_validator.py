"""Structural validator on WriteResponse.body_markdown."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wikify_simple.agents.schema import WriteResponse

_VALID_BODY = (
    "Atomic layer deposition builds films one half-cycle at a time[^e1].\n\n"
    "The technique is prized for its conformality and thickness control[^e2].\n\n"
    "## Evidence\n\n"
    "[^e1]: self-limiting surface reaction (doi:x)\n"
    "[^e2]: conformal coatings in trenches (doi:y)\n"
)


def _mk(body: str) -> WriteResponse:
    return WriteResponse(
        page_id="concept-ald",
        body_markdown=body,
        used_markers=["e1", "e2"],
        tokens_in=300,
        tokens_out=120,
    )


def test_valid_body_accepted():
    resp = _mk(_VALID_BODY)
    assert "## Evidence" in resp.body_markdown


def test_empty_body_rejected():
    with pytest.raises(ValidationError):
        _mk("")


def test_missing_evidence_heading_rejected():
    body = "Line one with marker[^e1].\n\nLine two with marker[^e2].\n"
    with pytest.raises(ValidationError):
        _mk(body)


def test_missing_markers_in_prose_rejected():
    body = (
        "Atomic layer deposition builds films one half-cycle at a time.\n\n"
        "The technique is prized for conformality and control.\n\n"
        "## Evidence\n\n"
        "[^e1]: a quote\n"
    )
    with pytest.raises(ValidationError):
        _mk(body)


def test_too_few_prose_lines_rejected():
    body = "Only one line of prose with a marker[^e1].\n\n## Evidence\n\n[^e1]: a quote\n"
    with pytest.raises(ValidationError):
        _mk(body)


def test_evidence_block_without_definitions_rejected():
    body = (
        "Line one with marker[^e1].\n\n"
        "Line two with marker[^e2].\n\n"
        "## Evidence\n\n"
        "this line is not a footnote definition\n"
    )
    with pytest.raises(ValidationError):
        _mk(body)
