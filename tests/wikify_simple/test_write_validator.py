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


_VALID_WITH_FIGURE = (
    "Atomic layer deposition builds films one half-cycle at a time[^e1].\n\n"
    "As shown in Figure 3, the growth is self-limiting[^e2].\n"
    "![Figure 3](images/doc1/fig3.png)\n\n"
    "## Evidence\n\n"
    "[^e1]: a quote\n"
    "[^e2]: another quote\n"
)


def test_figure_with_adjacent_mention_accepted():
    resp = _mk(_VALID_WITH_FIGURE)
    assert "Figure 3" in resp.body_markdown


def test_figure_without_adjacent_mention_rejected():
    body = (
        "Atomic layer deposition builds films[^e1].\n\n"
        "The concept is grounded in the cited chunks[^e2].\n\n"
        "![Figure 2](images/doc1/fig2.png)\n\n"
        "## Evidence\n\n"
        "[^e1]: q1\n[^e2]: q2\n"
    )
    with pytest.raises(ValidationError):
        _mk(body)


def test_no_figure_embed_accepted():
    # figures present in request but writer chose not to embed any
    resp = _mk(_VALID_BODY)
    assert "![" not in resp.body_markdown


def test_case_insensitive_figure_mention_accepted():
    body = (
        "Atomic layer deposition builds films[^e1].\n\n"
        "see fig 3 for the growth curve[^e2].\n"
        "![Figure 3](images/doc1/fig3.png)\n\n"
        "## Evidence\n\n"
        "[^e1]: q1\n[^e2]: q2\n"
    )
    resp = _mk(body)
    assert "fig 3" in resp.body_markdown


def test_evidence_block_without_definitions_rejected():
    body = (
        "Line one with marker[^e1].\n\n"
        "Line two with marker[^e2].\n\n"
        "## Evidence\n\n"
        "this line is not a footnote definition\n"
    )
    with pytest.raises(ValidationError):
        _mk(body)
