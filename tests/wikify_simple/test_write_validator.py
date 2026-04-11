"""Structural validator on WriteResponse.body_markdown.

The validator enforces a soft shape: at least one ``## H2`` heading,
>=3 prose paragraphs with at least one ``[^eN]`` marker, a final
``## References`` section with ``[^eN]:`` definitions, a 1200-char
body floor, the figure-mention rule, no ``[[wikilinks]]`` in prose,
and matched markers between prose and the references block. Specific
section names (Definition / Background / ...) are recommended, not
required.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from wikify_simple.bindings.fake import FakeWriter
from wikify_simple.contracts.schema import (
    WriteEvidenceRef,
    WriteRequest,
    WriteResponse,
)
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
    background: str | None = None,
    mechanism_sentences: list[str] | None = None,
    applications: str | None = None,
    open_questions: str | None = None,
    references_lines: list[str] | None = None,
) -> str:
    if background is None:
        background = (
            "Atomic layer deposition emerged from work on molecular layer "
            "epitaxy in the 1970s and was refined for industrial use over "
            "subsequent decades[^e1]. Early reports framed it as a route "
            "to conformal coatings on complex geometries[^e2]. The "
            "technique gained traction across semiconductor manufacturing "
            "and catalysis research over the following years[^e1]. "
            "Researchers refined precursor chemistry and reactor designs "
            "in parallel, expanding the available process window across "
            "many materials systems and substrate geometries[^e2]. "
            "The technique now anchors a broad area of thin-film "
            "engineering activity reflected throughout the corpus[^e1]. "
            "Reviews of the field cover precursor inventories, reactor "
            "designs, and growth-rate measurements across decades of "
            "published process work that this article summarises[^e2]."
        )
    if mechanism_sentences is None:
        mechanism_sentences = [
            "ALD proceeds through self-limiting half-reactions[^e1].",
            "Each half-reaction saturates the surface before the next pulse[^e2].",
            "The cycle is repeated to grow films one atomic layer at a time[^e1].",
            "Growth rates therefore depend on cycle count rather than exposure time[^e2].",
        ]
    if applications is None:
        applications = (
            "ALD is used to coat high-aspect-ratio structures in memory "
            "and logic devices[^e1]. It also enables conformal catalyst "
            "layers in heterogeneous catalysis[^e2]. Recent corpus "
            "sources discuss its role in neuromorphic memristor "
            "fabrication[^e1]. Industrial deployments span semiconductor "
            "fabs, photovoltaics, and protective barrier coatings on polymer "
            "substrates across multiple decades of process engineering "
            "work[^e2]. The technique continues to spread into new materials "
            "systems as new precursor chemistries are reported[^e1]."
        )
    if open_questions is None:
        open_questions = (
            "The corpus does not address how ALD scales to wafer-level memristor manufacturing."
        )
    if references_lines is None:
        references_lines = [
            '[^e1]: chunk_a (doc1) > "ALD self-limiting reaction"',
            '[^e2]: chunk_b (doc2) > "saturation criterion"',
        ]
    return (
        "# Atomic Layer Deposition\n\n"
        "## Definition\n\n"
        f"{definition}\n\n"
        "## Background\n\n"
        f"{background}\n\n"
        "## Mechanism / Process\n\n"
        + "\n\n".join(mechanism_sentences)
        + "\n\n## Applications\n\n"
        + applications
        + "\n\n## Open Questions\n\n"
        + open_questions
        + "\n\n## References\n\n"
        + "\n".join(references_lines)
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
    assert "## Background" in resp.body_markdown
    assert "## Applications" in resp.body_markdown
    assert "## References" in resp.body_markdown


# ---- length floor --------------------------------------------------------


def test_body_under_1200_chars_rejected() -> None:
    short = (
        "## Definition\n\nx\n\n## Background\n\na[^e1]. b[^e1]. c[^e1].\n\n"
        "## Mechanism\n\na[^e1]. b[^e1]. c[^e1]. d[^e1].\n\n"
        "## Applications\n\na[^e1]. b[^e1]. c[^e1].\n\n"
        "## Open Questions\n\nx\n\n## References\n\n[^e1]: q (d)\n"
    )
    with pytest.raises(ValidationError, match="1200"):
        _mk(short)


# ---- missing required shape ---------------------------------------------


def test_missing_references_rejected() -> None:
    body = _wiki_body().replace("## References", "## NotReferences")
    with pytest.raises(ValidationError, match="References"):
        _mk(body)


def test_body_with_no_h2_rejected() -> None:
    body = (
        "# Atomic Layer Deposition\n\n"
        + ("ALD is a thin-film growth technique used widely in semiconductors[^e1]. " * 40)
        + "\n\n"
        + ("It is self-limiting and deposits one atomic layer per cycle[^e2]. " * 20)
    )
    with pytest.raises(ValidationError):
        _mk(body)


# ---- custom sections accepted (no strict naming) ------------------------


def test_custom_section_names_accepted() -> None:
    """A body with non-canonical H2 names (e.g. Specifications,
    Crystal Structure) is accepted as long as the soft shape holds."""
    filler1 = (
        "This piece of equipment is used for atomic layer deposition and "
        "supports a range of precursor chemistries across many processes "
        "throughout the corpus of published work and industrial reports[^e1]. "
        "It is widely adopted in modern semiconductor manufacturing lines[^e2]. "
        "Reports describe its modular reactor geometry and vacuum hardware[^e1]. "
        "Several commercial vendors supply systems for research and production "
        "with a range of chamber sizes and precursor delivery options[^e2]. "
        "The corpus includes multiple primary sources characterising these "
        "systems across decades of process engineering practice[^e1]."
    )
    filler2 = (
        "The chamber is held at controlled temperature and pressure[^e1]. "
        "Precursor delivery is pulsed and separated by inert purge steps[^e2]. "
        "Cycle times are tuned to surface saturation limits[^e1]."
    )
    filler3 = (
        "Materials engineers characterise the resulting films using "
        "ellipsometry and X-ray reflectivity methods[^e1]. Additional "
        "crystallographic analysis is reported in several primary sources[^e2]. "
        "Crystal structures vary between amorphous and polycrystalline phases "
        "depending on deposition temperature and post-annealing conditions[^e1]."
    )
    body = (
        "# Example Equipment\n\n"
        "## Overview\n\n"
        f"{filler1}\n\n"
        "## Specifications\n\n"
        f"{filler2}\n\n"
        "## Crystal Structure\n\n"
        f"{filler3}\n\n"
        "## References\n\n"
        '[^e1]: chunk_a (doc1) > "ALD reactor"\n'
        '[^e2]: chunk_b (doc2) > "process details"\n'
    )
    resp = _mk(body)
    assert "## Specifications" in resp.body_markdown


# ---- wikilinks rejection -------------------------------------------------


def test_body_with_wikilink_rejected() -> None:
    body = _wiki_body().replace("ALD", "[[ALD]]", 1)
    with pytest.raises(ValidationError, match="wikilink"):
        _mk(body)


# ---- marker matching -----------------------------------------------------


def test_unmatched_prose_marker_rejected() -> None:
    body = _wiki_body(
        mechanism_sentences=[
            "ALD proceeds through self-limiting half-reactions[^e1].",
            "Each half-reaction saturates the surface[^e2].",
            "The cycle repeats forever[^e9].",
            "Growth scales with cycle count[^e1].",
        ]
    )
    with pytest.raises(ValidationError, match="e9"):
        _mk(body)


# ---- figure mention rule (preserved from prior validator) ----------------


def test_figure_with_adjacent_mention_accepted() -> None:
    mech = [
        "ALD proceeds through self-limiting half-reactions[^e1].",
        "Each half-reaction saturates the surface[^e2].",
        "Growth scales with cycle count[^e1].",
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
        "Growth scales with cycle count[^e1].",
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
        page_kind="article",
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
    WriteResponse.model_validate(resp.model_dump())


def test_fake_writer_with_figures_passes_validator(tmp_path: Path) -> None:
    from wikify_simple.contracts.schema import ImageRef

    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    figs = [ImageRef(id="doc/fig1", label="Figure 1", path="images/doc/fig1.png")]
    resp = fw.write(_request(n_evidence=2, figures=figs))
    WriteResponse.model_validate(resp.model_dump())
    assert "![Figure 1]" in resp.body_markdown


def test_fake_writer_output_has_no_wikilinks(tmp_path: Path) -> None:
    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    resp = fw.write(_request(n_evidence=2, neighbors=["A", "B", "C"]))
    WriteResponse.model_validate(resp.model_dump())
    assert "[[" not in resp.body_markdown


# ---- person page FakeWriter ---------------------------------------------


def _person_request(*, n_evidence: int = 3) -> WriteRequest:
    skeleton = (
        "**Alice Adams** is associated with testing in this corpus.\n\n"
        "## Notable contributions\n\n"
        "- [[Paper One]] — first contribution\n\n"
        "## Publications in this corpus\n\n"
        "- 2020. [[Paper One]]\n\n"
        "## Collaborators\n\n"
        "- [[Bob Brown]]\n"
    )
    return WriteRequest(
        page_id="Alice Adams",
        page_kind="person",
        title="Alice Adams",
        aliases=[],
        skeleton=skeleton,
        evidence=[_ev(i) for i in range(1, n_evidence + 1)],
        neighbor_titles=[],
        prompt_template="t",
        model_id="fake",
        tier="M",
    )


def test_fake_writer_person_page_passes_validator(tmp_path: Path) -> None:
    meter = _meter(tmp_path)
    fw = FakeWriter(meter)
    resp = fw.write(_person_request())
    WriteResponse.model_validate(resp.model_dump())
    body = resp.body_markdown
    # Tier 2 sections present.
    assert "## Research focus" in body
    assert "## Significance" in body
    # Tier 1 skeleton sections preserved (wikilinks stripped for validator).
    assert "## Publications in this corpus" in body
    assert "## Collaborators" in body
    assert "Paper One" in body
    assert "Bob Brown" in body
    # Wikilinks stripped from body (crosslink pass adds them via frontmatter).
    assert "[[" not in body
    # References present.
    assert "## References" in body
    assert "[^e1]:" in body
