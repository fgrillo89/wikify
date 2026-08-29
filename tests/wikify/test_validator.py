"""Tests for wikify.bundle.draft.validator — quote-grounding + structural checks."""

from __future__ import annotations

import json
from pathlib import Path

from tests.wikify.test_corpus_queries import _make_corpus  # noqa: E402
from wikify.api import Bundle, Corpus
from wikify.bundle.draft.artifact import (
    draft_path,
    read_json,
    response_path,
    validation_path,
    write_json,
)
from wikify.bundle.draft.builder import build_draft
from wikify.bundle.draft.references import normalize_response_references
from wikify.bundle.draft.validator import (
    validate_response,
    validate_response_data,
)
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence


def _setup(tmp_path: Path) -> tuple[Bundle, Corpus, str]:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    s, _ = create_concept(bundle, page_id="Atomic Layer Deposition", aliases=["ALD"])
    corpus = _make_corpus(tmp_path / "corpus")
    append_evidence(
        bundle, s, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(bundle, slug=s, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    return bundle, corpus, s


def _good_response(slug: str, *, chunk_quote: str) -> dict:
    """Build a structurally-valid WriteResponse around ``chunk_quote``.

    The body must clear the WriteResponse 1200-char minimum, so the
    sections are padded with realistic ALD prose.
    """
    body = (
        "## Lead\n\n"
        "Atomic Layer Deposition is a vapor-phase thin-film growth technique "
        "characterised by sequential self-limiting surface reactions between "
        "alternating precursor pulses [^e1]. The technique produces conformal "
        "coatings with sub-nanometre thickness control over arbitrarily complex "
        "three-dimensional substrates, which is why it is now central to gate-"
        "stack engineering, memristor fabrication, and area-selective patterning "
        "in advanced semiconductor nodes [^e1].\n\n"
        "## Mechanism\n\n"
        f"The standard ALD cycle exposes the substrate to two precursors in "
        f"separation, each pulse separated by an inert-gas purge that removes "
        f"unreacted molecules and gaseous byproducts [^e1]. {chunk_quote} The "
        f"self-limiting chemistry that defines an ALD half-reaction is what "
        f"distinguishes the technique from chemical vapor deposition, where "
        f"both reactants share the gas phase simultaneously and growth is "
        f"flux-limited rather than surface-limited [^e1]. Once the available "
        f"surface sites saturate, additional precursor exposure produces no "
        f"further growth, and the resulting one-monolayer-per-cycle ceiling "
        f"is what gives ALD its hallmark thickness control [^e1].\n\n"
        "## Applications\n\n"
        "ALD coats high-aspect-ratio trench structures uniformly because the "
        "vapor-phase precursors reach every surface site that the inert-gas "
        "purge can flush [^e1]. The dominant industrial applications are "
        "high-k gate dielectrics in CMOS transistors, atomic-layer etching, "
        "diffusion barriers in interconnect stacks, and resistive switching "
        "layers in memristive memory cells [^e1]. Area-selective ALD, where "
        "an inhibitor molecule blocks growth on a chosen surface, has emerged "
        "as a self-aligned alternative to lithographic patterning [^e1].\n\n"
        "## References\n\n"
        f'[^e1]: paper_0__c0000 (paper_0) > "{chunk_quote}"\n'
    )
    return {
        "schema_version": 1,
        "page_id": "Atomic Layer Deposition",
        "page_kind": "article",
        "body_markdown": body,
        "used_markers": ["e1"],
        "tokens_in": 1000,
        "tokens_out": 200,
    }


def _coverage_case(n_docs: int, cited: list[int]) -> tuple[dict, dict]:
    """Build a valid draft + response pair for the evidence-coverage check.

    The draft carries ``n_docs`` evidence records, each from a distinct
    document. The response cites the 0-based evidence indices in ``cited``
    (and only those), with grounded quotes so no correctness error fires and
    the evidence-coverage warning can be asserted in isolation.
    """
    evidence = [
        {
            "chunk_id": f"paper_{i}__c0000",
            "doc_id": f"paper_{i}",
            "quote": "",
            "chunk_text": (
                f"Fact number {i} about atomic layer deposition growth chemistry."
            ),
        }
        for i in range(n_docs)
    ]
    draft = {
        "page_id": "Atomic Layer Deposition",
        "page_kind": "article",
        "title": "Atomic Layer Deposition",
        "aliases": ["ALD"],
        "skeleton": "",
        "prompt_template": "",
        "model_id": "claude-sonnet-4-6",
        "tier": "M",
        "evidence": evidence,
    }
    markers = "".join(f" [^e{i + 1}]" for i in cited)
    ref_defs = "\n".join(
        f'[^e{i + 1}]: paper_{i}__c0000 (paper_{i}) > '
        f'"Fact number {i} about atomic layer deposition"'
        for i in cited
    )
    filler = (
        "Atomic layer deposition grows conformal thin films via sequential "
        "self-limiting surface reactions between alternating precursor pulses. "
    ) * 4
    body = (
        f"## Overview\n\n{filler}{markers}\n\n"
        f"## Mechanism\n\n{filler}{markers}\n\n"
        f"## Applications\n\n{filler}{markers}\n\n"
        f"## References\n\n{ref_defs}\n"
    )
    response = {
        "schema_version": 1,
        "page_id": "Atomic Layer Deposition",
        "page_kind": "article",
        "body_markdown": body,
        "used_markers": [f"e{i + 1}" for i in cited],
        "tokens_in": 1000,
        "tokens_out": 200,
    }
    return draft, response


def test_coverage_underuse_warns(tmp_path: Path) -> None:
    """6 available docs but only 2 cited -> evidence_underuse warning."""
    draft, response = _coverage_case(6, cited=[0, 1])
    verdict = validate_response_data(draft, response)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)  # warning, not error
    codes = [w["code"] for w in verdict["warnings"]]
    assert "evidence_underuse" in codes
    assert verdict["structural_checks"]["evidence_coverage"] is False


def test_coverage_enough_docs_no_warning(tmp_path: Path) -> None:
    """Citing ceil(0.5*available) docs clears the check -> no warning."""
    draft, response = _coverage_case(6, cited=[0, 1, 2])
    verdict = validate_response_data(draft, response)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    codes = [w["code"] for w in verdict["warnings"]]
    assert "evidence_underuse" not in codes
    assert verdict["structural_checks"]["evidence_coverage"] is True


def test_coverage_below_floor_no_warning(tmp_path: Path) -> None:
    """A narrow page (available_docs < 5) is exempt even if it cites one doc."""
    draft, response = _coverage_case(4, cited=[0])
    verdict = validate_response_data(draft, response)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    codes = [w["code"] for w in verdict["warnings"]]
    assert "evidence_underuse" not in codes
    assert verdict["structural_checks"]["evidence_coverage"] is True


def test_validate_ok_when_grounded(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    # Read the corpus chunk to extract a guaranteed-substring quote.
    draft_payload = read_json(draft_path(bundle, slug))
    chunk_text = draft_payload["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(slug, chunk_quote=quote))

    verdict = validate_response(bundle, slug)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert verdict["structural_checks"]["quote_grounding"] is True
    assert verdict["structural_checks"]["wikipedia_structure"] is True


def test_validate_fabricated_quote_rejected(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    fake = "this exact phrase is not in the chunk text at all"
    write_json(response_path(bundle, slug), _good_response(slug, chunk_quote=fake))

    verdict = validate_response(bundle, slug)
    assert verdict["ok"] is False
    codes = [e["code"] for e in verdict["errors"]]
    assert "quote_not_in_source" in codes


def test_normalize_references_rewrites_from_draft_evidence(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    fake = "this exact phrase is not in the chunk text at all"
    response = _good_response(slug, chunk_quote=fake)
    response["used_markers"] = ["e99"]
    write_json(response_path(bundle, slug), response)

    result = normalize_response_references(bundle, slug)

    assert result.markers == ["e1"]
    assert result.reference_count == 1
    normalized = read_json(response_path(bundle, slug))
    assert normalized["used_markers"] == ["e1"]
    assert "paper_0__c0000 (paper_0)" in normalized["body_markdown"]
    verdict = validate_response(bundle, slug)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)


def test_normalize_references_preserves_quotes_from_chunk_text(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    draft = read_json(draft_path(bundle, slug))
    draft["evidence"][0]["quote"] = ""
    draft["evidence"][0]["chunk_text"] = 'The device was called a "memristor" in the source.'
    write_json(draft_path(bundle, slug), draft)
    response = _good_response(slug, chunk_quote="fabricated quote")
    write_json(response_path(bundle, slug), response)

    normalize_response_references(bundle, slug)

    normalized = read_json(response_path(bundle, slug))
    assert 'called a "memristor"' in normalized["body_markdown"]
    verdict = validate_response(bundle, slug)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)


def test_validate_missing_marker_rejected(tmp_path: Path) -> None:
    """A response with NO `[^eN]` markers fails grounding."""
    bundle, _, slug = _setup(tmp_path)
    response = _good_response(slug, chunk_quote="anything")
    response["body_markdown"] = (
        "## Lead\n\nNo markers here.\n\n## Body\n\nStill no markers.\n\n"
        "## Applications\n\nNothing.\n\n## References\n\nempty.\n"
    )
    response["used_markers"] = []
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)
    assert verdict["ok"] is False


def test_validate_writes_validation_json(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    write_json(
        response_path(bundle, slug),
        _good_response(slug, chunk_quote=chunk_text[:30].strip()),
    )
    validate_response(bundle, slug)
    p = validation_path(bundle, slug)
    assert p.is_file()
    payload = read_json(p)
    assert "schema_version" in payload
    assert "structural_checks" in payload


def test_validate_selected_figure_must_match_draft_candidate(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    draft = read_json(draft_path(bundle, slug))
    draft["figures"] = [
        {
            "id": "paper_0/Figure_01",
            "label": "Figure 1",
            "caption": "Figure 1. ALD growth schematic.",
            "page": 2,
            "path": "images/paper_0/Figure_01.png",
            "near_chunk_ids": ["paper_0__c0000"],
        }
    ]
    write_json(draft_path(bundle, slug), draft)
    chunk_text = draft["evidence"][0]["chunk_text"]
    response = _good_response(slug, chunk_quote=chunk_text[:30].strip())
    response["body_markdown"] = response["body_markdown"].replace(
        "## Applications",
        "Figure 1 summarizes the deposition sequence.\n\n{{figure:fig1}}\n\n## Applications",
    )
    response["figures"] = [
        {
            "figure_id": "paper_0/Figure_01",
            "path": "images/paper_0/Figure_01.png",
            "caption": "Schematic overview of the ALD cycle.",
            "placement_anchor": "fig1",
            "source_marker": "e1",
        }
    ]
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)

    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert verdict["structural_checks"]["figure_selection"] is True


def test_validate_unknown_figure_placeholder_rejected(tmp_path: Path) -> None:
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    response = _good_response(slug, chunk_quote=chunk_text[:30].strip())
    response["body_markdown"] = response["body_markdown"].replace(
        "## Applications",
        "Figure 1 shows the relevant process.\n\n{{figure:missing}}\n\n## Applications",
    )
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)

    assert verdict["ok"] is False
    assert "unknown_figure_placeholder" in [e["code"] for e in verdict["errors"]]


def test_validate_undeclared_marker_flagged(tmp_path: Path) -> None:
    """Body uses [^e1] but used_markers list is empty: undeclared_prose_marker."""
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    response["used_markers"] = []
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)
    assert verdict["ok"] is False
    codes = [e["code"] for e in verdict["errors"]]
    assert "undeclared_prose_marker" in codes


def test_validate_literal_unicode_escape_rejected(tmp_path: Path) -> None:
    """A response containing a literal \\uXXXX escape in prose must fail check
    with code ``literal_unicode_escape``. Regression for the writer emitting
    six-character strings like ``\\u2013`` instead of the U+2013 en-dash.
    """
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    # Build the literal six-character escape sequence (backslash + u + 2013).
    # chr(0x5c) is the backslash character; this avoids Python string escape
    # interpretation so the string truly contains the six chars, not U+2013.
    literal_escape = chr(0x5C) + "u2013"
    response["body_markdown"] = response["body_markdown"].replace(
        "## Mechanism",
        f"## Mechanism {literal_escape} Overview",
    )
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)
    assert verdict["ok"] is False
    codes = [e["code"] for e in verdict["errors"]]
    assert "literal_unicode_escape" in codes
    assert verdict["structural_checks"]["no_unicode_escapes"] is False
    # The error message must name the escape sequence.
    msg = next(e["message"] for e in verdict["errors"] if e["code"] == "literal_unicode_escape")
    assert "u2013" in msg


def test_validate_stray_mapping_literal_rejected(tmp_path: Path) -> None:
    """A response with a leaked Python/JSON mapping literal in prose must fail
    with code ``stray_internal_machinery``. Regression for the writer pasting
    its citation-context scratch dict into the article body."""
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    blob = "accuracy of 90.16{'e1_cid': 'foo', 'e1_doc': 'bar'}fter training"
    response["body_markdown"] = response["body_markdown"].replace(
        "## Mechanism", f"## Mechanism\n\n{blob}\n",
    )
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)
    assert verdict["ok"] is False
    codes = [e["code"] for e in verdict["errors"]]
    assert "stray_internal_machinery" in codes
    assert verdict["structural_checks"]["no_stray_machinery"] is False


def test_validate_references_with_ids_not_flagged_as_machinery(tmp_path: Path) -> None:
    """The ``## References`` block carries chunk/doc ids; those must not trip
    the stray-machinery gate (it runs over reader-facing prose only)."""
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    write_json(response_path(bundle, slug), response)
    verdict = validate_response(bundle, slug)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert verdict["structural_checks"].get("no_stray_machinery") is True


def test_validate_real_unicode_char_ok(tmp_path: Path) -> None:
    """A body using the actual U+2013 character (not an escape) must pass."""
    bundle, _, slug = _setup(tmp_path)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    # Insert the real en-dash character U+2013, not an escape sequence.
    response["body_markdown"] = response["body_markdown"].replace(
        "## Mechanism",
        "## Mechanism – Overview",
    )
    write_json(response_path(bundle, slug), response)

    verdict = validate_response(bundle, slug)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert verdict["structural_checks"].get("no_unicode_escapes") is True


# ---------------------------------------------------------------------------
# `[^eN]` resolves POSITIONALLY to draft.evidence[N-1]
# ---------------------------------------------------------------------------


_SHARED_SENTENCE = (
    "The square-root law states that impact grows with the square root of "
    "the traded volume."
)


def _marker_case(*, cite_index: int, ref_body: str) -> tuple[dict, dict]:
    """A 3-entry draft whose evidence chunks SHARE one sentence.

    Every chunk carries ``_SHARED_SENTENCE``, so a quote drawn from it grounds
    against whichever entry the marker happens to select — exactly the case
    ``quote_grounding`` alone cannot distinguish from a correct citation.
    ``cite_index`` is the 0-based marker the prose uses; ``ref_body`` is the
    text placed between ``[^eN]:`` and the quote.
    """
    evidence = [
        {
            "chunk_id": f"paper_{i}__c0000",
            "doc_id": f"paper_{i}",
            "quote": "",
            "chunk_text": f"{_SHARED_SENTENCE} Paper {i} calibrates it on its own data.",
        }
        for i in range(3)
    ]
    draft = {
        "page_id": "Square Root Law",
        "page_kind": "article",
        "title": "Square Root Law",
        "aliases": [],
        "skeleton": "",
        "prompt_template": "",
        "model_id": "claude-sonnet-4-6",
        "tier": "M",
        "evidence": evidence,
    }
    marker = f"e{cite_index + 1}"
    filler = (
        "Market impact measures how much the price moves against a trader who "
        "executes a metaorder over an extended horizon. "
    ) * 4
    body = (
        f"## Overview\n\n{filler} [^{marker}]\n\n"
        f"## Mechanism\n\n{filler} [^{marker}]\n\n"
        f"## Calibration\n\n{filler} [^{marker}]\n\n"
        f'## References\n\n[^{marker}]: {ref_body}> "{_SHARED_SENTENCE}"\n'
    )
    response = {
        "schema_version": 1,
        "page_id": "Square Root Law",
        "page_kind": "article",
        "body_markdown": body,
        "used_markers": [marker],
        "tokens_in": 1000,
        "tokens_out": 200,
    }
    return draft, response


def test_marker_pointing_at_wrong_evidence_entry_is_rejected() -> None:
    """A writer that numbers markers freely produces a citation whose
    footnote names one chunk while the marker positionally selects another.
    The quote grounds against the selected entry anyway (the chunks overlap),
    so ``quote_grounding`` passed and the page shipped citing the wrong paper."""
    draft, response = _marker_case(cite_index=0, ref_body="paper_2__c0000 (paper_2) ")
    verdict = validate_response_data(draft, response)
    assert verdict["ok"] is False
    errs = [e for e in verdict["errors"] if e["code"] == "marker_evidence_mismatch"]
    assert len(errs) == 1
    # The error names BOTH indices so the fix is mechanical.
    assert "evidence[0]" in errs[0]["message"]
    assert "evidence[2]" in errs[0]["message"]
    assert "e3" in errs[0]["message"]
    assert verdict["structural_checks"]["quote_grounding"] is False


def test_marker_matching_its_evidence_entry_passes() -> None:
    """The same page with the marker numbered by position validates."""
    draft, response = _marker_case(cite_index=2, ref_body="paper_2__c0000 (paper_2) ")
    verdict = validate_response_data(draft, response)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert verdict["structural_checks"]["quote_grounding"] is True


def test_marker_mismatch_detected_from_quote_when_no_chunk_id() -> None:
    """With no chunk id in the footnote, a quote that is verbatim in exactly
    one OTHER entry reports the intended index rather than the generic
    'fabricated or corrupted citation'."""
    draft, response = _marker_case(cite_index=0, ref_body="")
    # Make the quote unique to evidence[2] so the fallback has one candidate.
    unique = "Paper 2 calibrates it on its own data."
    response["body_markdown"] = response["body_markdown"].replace(
        f'> "{_SHARED_SENTENCE}"', f'> "{unique}"'
    )
    verdict = validate_response_data(draft, response)
    assert verdict["ok"] is False
    errs = [e for e in verdict["errors"] if e["code"] == "marker_evidence_mismatch"]
    assert len(errs) == 1
    assert "evidence[2]" in errs[0]["message"]
    assert "e3" in errs[0]["message"]
    # A genuinely fabricated quote still reports quote_not_in_source.
    draft2, response2 = _marker_case(cite_index=0, ref_body="")
    response2["body_markdown"] = response2["body_markdown"].replace(
        f'> "{_SHARED_SENTENCE}"', '> "no such sentence appears in any chunk"'
    )
    codes = [e["code"] for e in validate_response_data(draft2, response2)["errors"]]
    assert "quote_not_in_source" in codes
    assert "marker_evidence_mismatch" not in codes


def test_normalize_references_refuses_to_repoint_a_mismatched_marker(
    tmp_path: Path,
) -> None:
    """``draft finalize`` runs normalize-references BEFORE check, and that step
    REWRITES every footnote from the marker index — so a mis-numbered marker
    silently acquired a well-formed citation to the wrong source and the
    grounding check that followed saw only the self-consistent result."""
    import pytest

    bundle, _, slug = _setup(tmp_path)
    draft_payload = read_json(draft_path(bundle, slug))
    # Add a second evidence entry so a marker can name the wrong one.
    first = draft_payload["evidence"][0]
    second = dict(first, chunk_id="paper_9__c0000", doc_id="paper_9")
    draft_payload["evidence"] = [first, second]
    write_json(draft_path(bundle, slug), draft_payload)

    quote = first["chunk_text"][:30].strip()
    response = _good_response(slug, chunk_quote=quote)
    # e1 selects evidence[0] but the definition names evidence[1]'s chunk.
    response["body_markdown"] = response["body_markdown"].replace(
        "[^e1]: paper_0__c0000 (paper_0)", "[^e1]: paper_9__c0000 (paper_9)"
    )
    write_json(response_path(bundle, slug), response)

    with pytest.raises(ValueError, match="POSITIONALLY"):
        normalize_response_references(bundle, slug)


def test_refine_build_reports_marker_remap() -> None:
    """`[^eN]` is positional, so a rebuild that reorders or drops evidence
    silently repoints every later marker. A refiner needs the old->new mapping
    to re-derive its markers deterministically."""
    from wikify.bundle.draft.builder import _marker_remap

    # Append-only: indices are stable, nothing to re-derive.
    assert _marker_remap({0: "a", 1: "b"}, ["a", "b", "c"]) == {
        "moved": {}, "dropped": [], "stable": True,
    }
    # A mid-array insert shifts every later marker by one.
    assert _marker_remap({0: "a", 1: "b"}, ["a", "x", "b"]) == {
        "moved": {"e2": "e3"}, "dropped": [], "stable": False,
    }
    # A dropped record both removes its marker and shifts the rest.
    assert _marker_remap({0: "a", 1: "b", 2: "c"}, ["a", "c"]) == {
        "moved": {"e3": "e2"}, "dropped": ["e2"], "stable": False,
    }
    # The committed-page baseline is SPARSE (a page cites a subset of its
    # evidence), so a gap must not be read as a shift.
    assert _marker_remap({0: "a", 2: "c"}, ["a", "b", "c"]) == {
        "moved": {}, "dropped": [], "stable": True,
    }


# ---------------------------------------------------------------------------
# Undelimited maths in prose (warning, not error)
# ---------------------------------------------------------------------------


def test_undelimited_math_warns_and_does_not_block() -> None:
    """Writers delimit DISPLAY equations but write inline symbols as bare
    prose, which renders as literal gibberish. Flag it without blocking."""
    from wikify.bundle.draft.validator import _undelimited_math_findings

    findings = _undelimited_math_findings(
        "The propagator G_{i,j} decays as (t_i - t_j)^(-b) while sigma_D "
        "grows, and impact scales like Q^(-5/2) with σ ≈ 0.5 and β ∝ Q.\n"
    )
    assert len(findings) == 1
    assert findings[0]["code"] == "undelimited_math"
    # G_{i,j}, t_i, t_j, sigma_D, Q^(-5/2), σ, ≈, β, ∝
    assert "9 mathematical symbol(s)" in findings[0]["message"]


def test_undelimited_math_detector_shapes() -> None:
    """The detector must catch spelled-out Greek with a script (`sigma_D`) —
    its own motivating case — without sweeping up snake_case identifiers, and
    must not be silenced by currency amounts or an inch mark pairing into a
    fake math / quotation span."""
    from wikify.bundle.draft.validator import _undelimited_math_findings

    def count(text: str) -> int:
        found = _undelimited_math_findings(text)
        return int(found[0]["message"].split()[0]) if found else 0

    assert count("The value sigma_D grows and Q_nu holds.") == 2
    assert count("Here t_i and Q^2 and G_{i,j} appear.") == 3
    assert count("The Reynolds Re_x value.") == 1
    # snake_case prose is not maths
    assert count("The is_boilerplate flag and to_date column.") == 0
    # Two dollar amounts must not pair into a math span that blanks the
    # symbols between them - market-impact prose is full of dollar amounts.
    # The span between these two carries no maths character, so the
    # maths-content lookahead alone refuses to pair them.
    assert count(
        "A $10 million order moves price by σ ≈ 0.5 β, while a $1 million "
        "order barely registers."
    ) == 3
    # ...and this is the case the lookahead CANNOT refuse: the subscript the
    # detector is hunting for is itself the maths character that would let the
    # span pair, so only the digit guard on the opening `$` stops it.
    assert count(
        "A $10 million order moves price by sigma_D, while a $5 million order "
        "moves t_i."
    ) == 2
    # An inch mark must not pair with the next real quotation.
    assert count('The 6" contract and σ rose, see "Kyle (1985)" for detail.') == 1


def test_undelimited_math_ignores_delimited_and_quoted_regions() -> None:
    """Math regions, fenced code, inline code, verbatim quotations, and the
    ``## References`` block are all exempt — a quote reproduces the source's
    own notation and is not the writer's to re-delimit."""
    from wikify.bundle.draft.validator import _undelimited_math_findings

    assert _undelimited_math_findings(
        "The propagator $G_{i,j}$ decays as $(t_i - t_j)^{-\\beta}$ and "
        "$$\\sigma_D = \\sqrt{V}$$ holds, with \\(Q^{-5/2}\\) and "
        "\\[\\alpha \\approx 0.5\\].\n"
        "A film of 100 µm and a 5 μs pulse stay plain text.\n"
        "```\nx_1 = sigma\n```\n"
        'The paper writes "the exponent β ≈ 0.5 in their sample".\n'
        "Inline `Q^2` code is exempt too.\n"
        "\n## References\n\n"
        '[^e1]: chunk_a (doc_a) > "we find β ≈ 0.5 with σ_D fixed"\n'
    ) == []


def test_undelimited_math_surfaces_as_a_non_blocking_verdict_warning() -> None:
    draft, response = _marker_case(cite_index=0, ref_body="paper_0__c0000 (paper_0) ")
    response["body_markdown"] = response["body_markdown"].replace(
        "## Calibration\n\n",
        "## Calibration\n\nThe fitted exponent is β ≈ 0.5 for sigma_D. ",
    )
    verdict = validate_response_data(draft, response)
    assert verdict["ok"], json.dumps(verdict["errors"], indent=2)
    assert [w["code"] for w in verdict["warnings"]] == ["undelimited_math"]
    assert verdict["structural_checks"]["math_delimited"] is False


def test_normalizer_and_validator_agree_on_marker_mismatch() -> None:
    """`draft check` and `normalize-references` implement the same rule and MUST
    reach the same verdict: normalization rewrites definitions FROM the marker
    index, so any mismatch it cannot see is one it silently repoints. They drifted
    once already — the normalizer's regex lacked DOTALL, so a quote wrapped onto a
    second line parsed in the validator and not in the normalizer."""
    from wikify.bundle.draft.references import (
        _assert_markers_match_evidence,
        parse_ref_chunk_ids,
        parse_ref_defs,
    )
    from wikify.bundle.draft.schema import WriteRequest
    from wikify.bundle.draft.validator import _marker_mismatch_error

    def draft_with(chunk_ids: list[str]) -> WriteRequest:
        return WriteRequest.model_validate({
            "page_id": "P", "page_kind": "article", "title": "P", "aliases": [],
            "skeleton": "", "prompt_template": "", "model_id": "m", "tier": "M",
            "evidence": [
                {"chunk_id": c, "doc_id": "d", "quote": "", "chunk_text": "text"}
                for c in chunk_ids
            ],
        })

    draft = draft_with(["cA", "cB"])
    bodies = {
        # quote wrapped onto a second line, marker e1 wrongly names cB
        "wrapped": '## References\n\n[^e1]: cB (d) > "a quote the writer\nwrapped"\n',
        # definition placed BEFORE the References heading
        "pre-heading": '[^e1]: cB (d) > "q"\n\n## References\n',
        # correct
        "correct": '## References\n\n[^e1]: cA (d) > "q"\n',
        # duplicate chunk: e1 correctly names the first occurrence
        "duplicate": '## References\n\n[^e1]: cA (d) > "q"\n',
    }
    drafts = {k: draft for k in bodies} | {"duplicate": draft_with(["cA", "cB", "cA"])}

    for name, body in bodies.items():
        d = drafts[name]
        # Ask the VALIDATOR itself, not a re-implementation of its rule: a
        # test that derives the expected answer from the shared parser can
        # only detect drift in that parser, never in the check it is named
        # for. Verified by disabling `_marker_mismatch_error` — this must fail.
        validator_flags = any(
            _marker_mismatch_error(
                idx, d, cid, parse_ref_defs(body).get(idx, ("", ""))[1]
            ) is not None
            for idx, cid in parse_ref_chunk_ids(body).items()
        )
        try:
            _assert_markers_match_evidence(body, d)
            normalizer_flags = False
        except ValueError:
            normalizer_flags = True
        assert normalizer_flags == validator_flags, name

    # The two mis-numbered cases must actually be caught, not merely agree on "no".
    for name in ("wrapped", "pre-heading"):
        try:
            _assert_markers_match_evidence(bodies[name], drafts[name])
            raise AssertionError(f"{name} was not rejected")
        except ValueError:
            pass


def test_malformed_definition_cannot_hide_the_ones_after_it() -> None:
    """Definitions are parsed block-by-block, not with one multi-line regex.
    With DOTALL, a definition that cannot close on its own line (no `> "quote"`
    tail, or an odd number of quote characters) runs past itself and swallows
    every definition after it. Both the validator and the normalizer read these
    through the same parser, so they would go blind TOGETHER — agreeing while
    both were wrong — and normalization would then rewrite the swallowed
    definitions from the marker index, silently repointing the exact citations
    the mismatch check exists to catch."""
    from wikify.bundle.draft.references import parse_ref_chunk_ids, parse_ref_defs

    swallowing = (
        '## References\n\n'
        '[^e1]: ch_a (d1) > "unterminated\n'
        '[^e2]: ch_b (d1) > "a real quote."\n'
    )
    assert parse_ref_chunk_ids(swallowing) == {1: "ch_b"}
    assert 0 not in parse_ref_defs(swallowing)  # malformed, not "absent"

    # Shapes that must still parse: a quote wrapped onto a second line, a
    # definition before the heading, and a quote containing quote characters.
    assert parse_ref_chunk_ids(
        '## References\n\n[^e1]: ch_a (d1) > "a quote the writer\nwrapped"\n'
    ) == {0: "ch_a"}
    assert parse_ref_chunk_ids('[^e1]: ch_a (d1) > "q"\n\n## References\n') == {
        0: "ch_a"
    }
    assert parse_ref_defs('[^e1]: ch_a (d1) > "he called it "c" here"\n')[0][1] == (
        'he called it "c" here'
    )
