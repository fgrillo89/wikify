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
from wikify.bundle.draft.validator import validate_response
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
