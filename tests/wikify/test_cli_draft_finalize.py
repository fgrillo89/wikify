"""Tests for `wikify draft finalize` — the per-page commit macro."""

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
    validation_path,
    write_json,
)
from wikify.bundle.work.claim import acquire_claim, read_claim
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


def _good_response(chunk_quote: str) -> dict:
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
        f"self-limiting chemistry is what distinguishes ALD from CVD [^e1].\n\n"
        "## Applications\n\n"
        "ALD coats high-aspect-ratio trench structures uniformly because the "
        "vapor-phase precursors reach every surface site [^e1]. The dominant "
        "industrial applications are high-k gate dielectrics, atomic-layer "
        "etching, diffusion barriers in interconnect stacks, and resistive "
        "switching layers in memristive memory cells [^e1]. Area-selective ALD "
        "has emerged as a self-aligned alternative to lithographic patterning "
        "[^e1].\n\n"
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


def _build_draft(bundle_dir: Path, corpus_dir: Path, slug: str) -> None:
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


def test_draft_finalize_happy_path(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    _build_draft(bundle_dir, corpus_dir, slug)
    bundle = Bundle.open(bundle_dir)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(quote))
    # Claim the concept so release has something concrete to release.
    owner = "test-finalize-owner"
    acquire_claim(bundle, slug, owner=owner)

    result = runner.invoke(
        app,
        [
            "draft", "finalize", slug,
            "--run", str(bundle_dir),
            "--owner", owner,
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["slug"] == slug
    step_names = [s["step"] for s in envelope["steps"]]
    assert step_names == ["normalize-references", "check", "commit", "release"]
    assert all(s["ok"] for s in envelope["steps"])

    # The page must be written under wiki/articles/.
    article_dir = bundle.root / "wiki" / "articles"
    article_files = list(article_dir.iterdir())
    assert any(p.name == f"{slug}.md" for p in article_files), article_files
    commit_step = next(s for s in envelope["steps"] if s["step"] == "commit")
    assert commit_step["path"] == f"wiki/articles/{slug}.md"

    # The per-attempt artifacts must be garbage-collected by commit.
    assert not draft_path(bundle, slug).exists()
    assert not response_path(bundle, slug).exists()
    assert not validation_path(bundle, slug).exists()

    # The claim must have been released.
    assert read_claim(bundle, slug) is None


def test_draft_finalize_validation_failure(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    _build_draft(bundle_dir, corpus_dir, slug)
    bundle = Bundle.open(bundle_dir)
    # A stub body that's well under the 1200-char minimum fails the
    # structural check. normalize-references happily rewrites the
    # References block, then check rejects.
    response = {
        "schema_version": 1,
        "page_id": "Atomic Layer Deposition",
        "page_kind": "article",
        "body_markdown": (
            "## Lead\n\nA tiny stub [^e1].\n\n"
            "## References\n\n"
            '[^e1]: paper_0__c0000 (paper_0) > "stub"\n'
        ),
        "used_markers": ["e1"],
        "tokens_in": 10,
        "tokens_out": 5,
    }
    write_json(response_path(bundle, slug), response)
    owner = "test-finalize-owner"

    result = runner.invoke(
        app,
        [
            "draft", "finalize", slug,
            "--run", str(bundle_dir),
            "--owner", owner,
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output

    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    failing = envelope["steps"][-1]
    assert failing["step"] == "check"
    assert failing["ok"] is False
    # commit/release must NOT have run.
    assert [s["step"] for s in envelope["steps"]] == [
        "normalize-references",
        "check",
    ]

    # No wiki page should exist.
    article_dir = bundle.root / "wiki" / "articles"
    assert not article_dir.exists() or not any(article_dir.iterdir())


def test_draft_finalize_dry_run(tmp_path: Path) -> None:
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    _build_draft(bundle_dir, corpus_dir, slug)
    bundle = Bundle.open(bundle_dir)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(quote))
    # Snapshot mutation-relevant disk state before invoking finalize.
    response_before = response_path(bundle, slug).read_text(encoding="utf-8")
    article_dir = bundle.root / "wiki" / "articles"
    pre_articles = list(article_dir.iterdir()) if article_dir.exists() else []

    result = runner.invoke(
        app,
        [
            "draft", "finalize", slug,
            "--run", str(bundle_dir),
            "--owner", "test-finalize-owner",
            "--format", "json",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output

    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["dry_run"] is True
    assert [s["step"] for s in envelope["steps"]] == [
        "normalize-references",
        "check",
        "commit",
        "release",
    ]
    assert all(s.get("planned") for s in envelope["steps"])

    # Nothing on disk should have changed.
    assert response_path(bundle, slug).read_text(encoding="utf-8") == response_before
    assert not validation_path(bundle, slug).exists()
    post_articles = list(article_dir.iterdir()) if article_dir.exists() else []
    assert post_articles == pre_articles


def test_draft_finalize_wrong_owner_does_not_mutate(tmp_path: Path) -> None:
    """A live claim held by another owner must gate every step."""
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    _build_draft(bundle_dir, corpus_dir, slug)
    bundle = Bundle.open(bundle_dir)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(quote))

    # Another owner holds the claim.
    acquire_claim(bundle, slug, owner="other-owner", ttl_seconds=1800)

    article_dir = bundle.root / "wiki" / "articles"
    pre_articles = list(article_dir.iterdir()) if article_dir.exists() else []
    pre_validation_exists = validation_path(bundle, slug).exists()

    result = runner.invoke(
        app,
        [
            "draft", "finalize", slug,
            "--run", str(bundle_dir),
            "--owner", "intruder",
            "--format", "json",
        ],
    )
    # EXIT_LOCK_HELD (2); envelope reports the claim-check step.
    assert result.exit_code == 2, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["steps"][0]["step"] == "claim-check"
    assert envelope["steps"][0]["error"] == "claim_held"
    assert envelope["steps"][0]["owner"] == "other-owner"

    # No mutation happened: no validation.json was written, no article was
    # promoted, and the other owner's claim is still intact.
    assert validation_path(bundle, slug).exists() == pre_validation_exists
    post_articles = list(article_dir.iterdir()) if article_dir.exists() else []
    assert post_articles == pre_articles
    still_held = read_claim(bundle, slug)
    assert still_held and still_held.get("owner") == "other-owner"


def test_draft_finalize_default_owner(tmp_path: Path) -> None:
    """draft finalize succeeds without --owner; defaults to 'investigate'."""
    bundle_dir, corpus_dir, slug = _setup_bundle_with_concept(tmp_path)
    _build_draft(bundle_dir, corpus_dir, slug)
    bundle = Bundle.open(bundle_dir)
    chunk_text = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, slug), _good_response(quote))
    # Acquire the claim with the expected default owner.
    acquire_claim(bundle, slug, owner="investigate")

    result = runner.invoke(
        app,
        [
            "draft", "finalize", slug,
            "--run", str(bundle_dir),
            # no --owner
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    # Claim must be released.
    assert read_claim(bundle, slug) is None
