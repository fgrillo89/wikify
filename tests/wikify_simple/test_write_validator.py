"""Structural validator on WriteResponse.body_markdown.

The validator enforces the full Wikipedia-style six-section layout
produced by ``prompts/write_v1.yaml``: every required heading must be
present in order, with per-section minimums (sentence/bullet counts),
an 800-char body floor, the original figure-mention rule, and matched
``[^eN]`` markers between prose and the evidence block.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify_simple.agents.schema import (
    WriteEvidenceRef,
    WriteRequest,
    WriteResponse,
)
from wikify_simple.bindings.fake import FakeWriter
from wikify_simple.infra.cost_meter import CostMeter


def _meter(tmp_path: Path) -> CostMeter:
    return CostMeter(
        budget_haiku_eq=10_000.0,
        run_id="test-write",
        events_path=tmp_path / "events.jsonl",
    )


def _wiki_body(
    *,
    definition: str = (
        "Atomic layer deposition is a vapor-phase thin-film growth "
        "technique used to build conformal coatings."
    ),
    mechanism_sentences: list[str] | None = None,
    facts: list[str] | None = None,
    in_corpus: str | None = None,
    relationships: str | None = None,
    open_questions: str | None = None,
    evidence_lines: list[str] | None = None,
) -> str:
    if mechanism_sentences is None:
        mechanism_sentences = [
            "ALD proceeds through self-limiting half-reactions[^e1].",
            "Each half-reaction saturates the surface before the next pulse[^e2].",
            "The cycle is repeated to grow films one atomic layer at a time[^e1].",
        ]
    if facts is None:
        facts = [
            "- ALD produces conformal films on high-aspect-ratio structures[^e1].",
            "- Growth rates are typically below one Angstrom per cycle[^e2].",
            "- Process temperatures span roughly 50 to 400 C[^e1].",
        ]
    if in_corpus is None:
        in_corpus = (
            "The corpus emphasises ALD as a route to ultrathin films "
            "for memristor and neuromorphic devices[^e1]. "
            "Multiple sources discuss the cycle saturation criterion[^e2]."
        )
    if relationships is None:
        relationships = (
            "| Related Concept | Relation |\n"
            "|-----------------|----------|\n"
            "| [[Memristor]]   | related  |\n"
        )
    if open_questions is None:
        open_questions = (
            "The corpus does not address how ALD scales to wafer-level memristor manufacturing."
        )
    if evidence_lines is None:
        evidence_lines = [
            '[^e1]: chunk_a (doc1) > "ALD self-limiting reaction"',
            '[^e2]: chunk_b (doc2) > "saturation criterion"',
        ]
    return (
        "# Atomic Layer Deposition\n\n"
        "## Definition\n\n"
        f"{definition}\n\n"
        "## Mechanism / Process\n\n"
        + "\n\n".join(mechanism_sentences)
        + "\n\n## Key Facts\n\n"
        + "\n".join(facts)
        + "\n\n## In This Corpus\n\n"
        + in_corpus
        + "\n\n## Relationships\n\n"
        + relationships
        + "\n\n## Open Questions\n\n"
        + open_questions
        + "\n\n## Evidence\n\n"
        + "\n".join(evidence_lines)
        + "\n"
    )


def _mk(body: str) -> WriteResponse:
    return WriteResponse(
        page_id="concept-ald",
        body_markdown=body,
        used_markers=["e1", "e2"],
        tokens_in=300,
        tokens_out=120,
    )


# ---- happy path ----------------------------------------------------------


def test_valid_full_wiki_body_accepted() -> None:
    resp = _mk(_wiki_body())
    assert "## Definition" in resp.body_markdown
    assert "## Open Questions" in resp.body_markdown


# ---- length floor --------------------------------------------------------


def test_body_under_800_chars_rejected() -> None:
    short = (
        "## Definition\n\nx\n\n## Mechanism\n\na[^e1]. b[^e1]. c[^e1].\n\n"
        "## Key Facts\n\n- a[^e1]\n- b[^e1]\n- c[^e1]\n\n"
        "## In This Corpus\n\nx[^e1]\n\n## Relationships\n\n| a | b |\n\n"
        "## Open Questions\n\nx\n\n## Evidence\n\n[^e1]: q (d)\n"
    )
    with pytest.raises(ValidationError, match="800"):
        _mk(short)


# ---- missing sections ----------------------------------------------------


def test_missing_definition_rejected() -> None:
    body = _wiki_body().replace("## Definition\n\n", "## Foo\n\n", 1)
    with pytest.raises(ValidationError, match="Definition"):
        _mk(body)


def test_missing_open_questions_rejected() -> None:
    body = _wiki_body().replace("## Open Questions", "## Other Stuff")
    with pytest.raises(ValidationError, match="Open Questions"):
        _mk(body)


def test_missing_evidence_heading_rejected() -> None:
    body = _wiki_body().replace("## Evidence", "## NotEvidence")
    with pytest.raises(ValidationError, match="Evidence"):
        _mk(body)


# ---- per-section minimums ------------------------------------------------


def test_mechanism_with_one_sentence_rejected() -> None:
    body = _wiki_body(mechanism_sentences=["ALD is a deposition technique[^e1]."])
    with pytest.raises(ValidationError, match="Mechanism.*3 sentences"):
        _mk(body)


def test_key_facts_with_two_bullets_rejected() -> None:
    body = _wiki_body(
        facts=[
            "- ALD is conformal[^e1].",
            "- ALD has slow growth rates[^e2].",
        ]
    )
    with pytest.raises(ValidationError, match="Key Facts.*3"):
        _mk(body)


# ---- marker matching -----------------------------------------------------


def test_unmatched_prose_marker_rejected() -> None:
    body = _wiki_body(
        mechanism_sentences=[
            "ALD proceeds through self-limiting half-reactions[^e1].",
            "Each half-reaction saturates the surface[^e2].",
            "The cycle repeats forever[^e9].",
        ]
    )
    with pytest.raises(ValidationError, match="e9"):
        _mk(body)


# ---- figure mention rule (preserved from prior validator) ----------------


def test_figure_with_adjacent_mention_accepted() -> None:
    mech = [
        "ALD proceeds through self-limiting half-reactions[^e1].",
        "Each half-reaction saturates the surface[^e2].",
        (
            "As shown in Figure 3, the cycle is reproducible[^e1].\n"
            "![Figure 3](images/doc1/fig3.png)"
        ),
    ]
    resp = _mk(_wiki_body(mechanism_sentences=mech))
    assert "Figure 3" in resp.body_markdown


def test_figure_without_adjacent_mention_rejected() -> None:
    mech = [
        "ALD proceeds through self-limiting half-reactions[^e1].",
        "Each half-reaction saturates the surface[^e2].",
        "The cycle is reproducible[^e1].\n![Figure 3](images/doc1/fig3.png)",
    ]
    with pytest.raises(ValidationError, match="Figure 3"):
        _mk(_wiki_body(mechanism_sentences=mech))


# ---- FakeWriter end-to-end ----------------------------------------------


def _ev(n: int) -> WriteEvidenceRef:
    return WriteEvidenceRef(
        chunk_id=f"chunk_{n}",
        doc_id=f"doc_{n}",
        quote=f"quote {n}",
        locator="",
    )


def _request(
    *,
    n_evidence: int = 2,
    figures: list | None = None,
    neighbors: list[str] | None = None,
) -> WriteRequest:
    return WriteRequest(
        page_id="concept-x",
        page_kind="concept",
        title="Example Concept",
        aliases=[],
        skeleton="",
        evidence=[_ev(i) for i in range(1, n_evidence + 1)],
        neighbor_titles=neighbors or [],
        prompt_template="t",
        model_id="fake",
        tier="M",
        figures=figures or [],
    )


def test_fake_writer_minimal_evidence_passes_validator(tmp_path: Path) -> None:
    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    resp = fw.write(_request(n_evidence=1))
    # round-trip through the validator
    WriteResponse.model_validate(resp.model_dump())


def test_fake_writer_with_figures_passes_validator(tmp_path: Path) -> None:
    from wikify_simple.agents.schema import ImageRef

    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    figs = [ImageRef(id="doc/fig1", label="Figure 1", path="images/doc/fig1.png")]
    resp = fw.write(_request(n_evidence=2, figures=figs))
    WriteResponse.model_validate(resp.model_dump())
    assert "![Figure 1]" in resp.body_markdown


def test_fake_writer_with_neighbors_passes_validator(tmp_path: Path) -> None:
    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    resp = fw.write(_request(n_evidence=2, neighbors=["A", "B", "C"]))
    WriteResponse.model_validate(resp.model_dump())
    assert "[[A]]" in resp.body_markdown
