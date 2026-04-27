"""Tests for `wikify draft ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_corpus_queries import _make_corpus  # noqa: E402
from wikify.api import Bundle
from wikify.bundle.draft.artifact import (
    draft_path,
    read_json,
    response_path,
    write_json,
)
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence
from wikify.cli import app

runner = CliRunner()


def _setup_bundle_with_concept(tmp_path: Path) -> tuple[Path, Path, str]:
    bundle_dir = tmp_path / "bundle"
    corpus_dir = tmp_path / "corpus"
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(corpus_dir)],
    )
    runner.invoke(
        app,
        [
            "work", "add", "concept",
            "Atomic Layer Deposition",
            "--run", str(bundle_dir),
            "--aliases", '["ALD"]',
        ],
    )
    bundle = Bundle.open(bundle_dir)
    append_evidence(
        bundle,
        "atomic-layer-deposition",
        [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")],
    )
    _make_corpus(corpus_dir)
    return bundle_dir, corpus_dir, "atomic-layer-deposition"


def test_draft_build(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    result = runner.invoke(
        app,
        [
            "draft", "build", slug,
            "--run", str(bundle_dir),
            "--corpus", str(corpus_dir),
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["page_id"] == "Atomic Layer Deposition"
    assert data["evidence_count"] == 1


def test_draft_show_after_build(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    runner.invoke(
        app,
        [
            "draft", "build", slug,
            "--run", str(bundle_dir),
            "--corpus", str(corpus_dir),
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
        ],
    )
    result = runner.invoke(app, ["draft", "show", slug, "--run", str(bundle_dir)])
    assert result.exit_code == 0
    assert "Atomic Layer Deposition" in result.output


def test_draft_show_missing(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    corpus_dir = tmp_path / "corpus"
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(corpus_dir)],
    )
    result = runner.invoke(
        app, ["draft", "show", "no-such", "--run", str(bundle_dir)]
    )
    assert result.exit_code != 0


def _good_response(slug: str, chunk_quote: str) -> dict:
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
        f"flux-limited rather than surface-limited [^e1].\n\n"
        "## Applications\n\n"
        "ALD coats high-aspect-ratio trench structures uniformly because the "
        "vapor-phase precursors reach every surface site that the inert-gas "
        "purge can flush [^e1]. Industrial applications include high-k gate "
        "dielectrics in CMOS, diffusion barriers, and resistive switching "
        "layers in memristive memory cells [^e1].\n\n"
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


def test_draft_check_passes(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    runner.invoke(
        app,
        [
            "draft", "build", slug,
            "--run", str(bundle_dir),
            "--corpus", str(corpus_dir),
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
        ],
    )
    bundle = Bundle.open(bundle_dir)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(slug, quote))

    result = runner.invoke(
        app,
        ["draft", "check", slug, "--run", str(bundle_dir), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    verdict = json.loads(result.output)
    assert verdict["ok"] is True


def test_draft_check_fails_on_fabricated_quote(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    runner.invoke(
        app,
        [
            "draft", "build", slug,
            "--run", str(bundle_dir),
            "--corpus", str(corpus_dir),
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
        ],
    )
    bundle = Bundle.open(bundle_dir)
    write_json(
        response_path(bundle, slug),
        _good_response(slug, "this phrase is not in the chunk text"),
    )
    result = runner.invoke(
        app, ["draft", "check", slug, "--run", str(bundle_dir)]
    )
    assert result.exit_code != 0


def test_draft_check_missing_response(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    runner.invoke(
        app,
        [
            "draft", "build", slug,
            "--run", str(bundle_dir),
            "--corpus", str(corpus_dir),
            "--model-id", "claude-sonnet-4-6",
            "--tier", "M",
        ],
    )
    # No response.json written yet.
    result = runner.invoke(
        app, ["draft", "check", slug, "--run", str(bundle_dir)]
    )
    assert result.exit_code != 0
