"""Tests for wikify.bundle.wiki.commit — the wiki commit gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.wikify.test_corpus_queries import _make_corpus  # noqa: E402
from wikify.api import Bundle
from wikify.bundle.draft.artifact import (
    draft_path,
    read_json,
    response_path,
    validation_path,
    write_json,
)
from wikify.bundle.draft.builder import build_draft
from wikify.bundle.draft.validator import validate_response
from wikify.bundle.run.events import read_events
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.wiki.commit import CommitGateError, commit_page
from wikify.bundle.wiki.page import parse_page
from wikify.bundle.work.card import create_concept, load_card
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence


def _good_response_payload(chunk_quote: str) -> dict:
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
        "industrial applications are high-k gate dielectrics, atomic-layer etching, "
        "diffusion barriers in interconnect stacks, and resistive switching layers "
        "in memristive memory cells [^e1]. Area-selective ALD has emerged as a "
        "self-aligned alternative to lithographic patterning [^e1].\n\n"
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


def _setup_validated(tmp_path: Path) -> tuple[Bundle, str]:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path="data/corpora/foo")
    s, _ = create_concept(bundle, page_id="Atomic Layer Deposition", aliases=["ALD"])
    corpus = _make_corpus(tmp_path / "corpus")
    append_evidence(
        bundle, s, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(bundle, slug=s, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    chunk_text = read_json(draft_path(bundle, s))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    write_json(response_path(bundle, s), _good_response_payload(quote))
    validate_response(bundle, s)
    return bundle, s


def test_commit_writes_wiki_page(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    result = commit_page(bundle, slug=slug)
    assert result.page_path.is_file()
    assert "wiki/articles" in str(result.page_path).replace("\\", "/")
    text = result.page_path.read_text(encoding="utf-8")
    assert "Atomic Layer Deposition" in text
    assert "links: []" in text


def test_commit_updates_concept_card(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    commit_page(bundle, slug=slug)
    card = load_card(bundle, slug)
    assert card.front["status"] == "committed"
    assert "wiki_path" in card.front


def test_commit_garbage_collects_attempt(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    assert draft_path(bundle, slug).is_file()
    assert response_path(bundle, slug).is_file()
    assert validation_path(bundle, slug).is_file()
    commit_page(bundle, slug=slug)
    assert not draft_path(bundle, slug).exists()
    assert not response_path(bundle, slug).exists()
    assert not validation_path(bundle, slug).exists()


def test_commit_embeds_page_for_mid_loop_semantic_search(tmp_path: Path) -> None:
    """F26: the page is embedded at commit, so semantic wiki_find works the
    next round instead of only after the finalize `wiki rebuild`."""
    from wikify.bundle.wiki.queries import find_semantic
    from wikify.bundle.wiki.store import open_wiki_store

    bundle, slug = _setup_validated(tmp_path)
    commit_page(bundle, slug=slug)
    con = open_wiki_store(bundle.sqlite_path)
    try:
        n_vec = con.execute("SELECT COUNT(*) FROM wiki_embeddings").fetchone()[0]
    finally:
        con.close()
    assert n_vec >= 1, "committed page must be embedded at commit time"
    # Semantic search finds it with no rebuild having run.
    hits = find_semantic(bundle, "atomic layer deposition cycle", top_k=5)
    assert any(h.get("page_id") == "Atomic Layer Deposition" for h in hits)


def test_commit_records_embedding_failure_instead_of_swallowing(
    tmp_path: Path, monkeypatch
) -> None:
    """The commit-time embedding is best-effort, but a failure must be observable
    (a page_embedding_failed event), not silently swallowed — and must not fail
    the commit."""
    import wikify.bundle.wiki.derived as derived

    def boom(_bundle, _page):
        raise RuntimeError("embedder unavailable")

    monkeypatch.setattr(derived, "embed_committed_page", boom)
    bundle, slug = _setup_validated(tmp_path)
    # Commit still succeeds despite the embedder blowing up.
    result = commit_page(bundle, slug=slug)
    assert result.page_path.is_file()
    events = read_events(bundle)
    # page_committed is recorded (inside the lock) regardless of the later,
    # post-lock embedding outcome.
    assert any(e.type == "page_committed" for e in events)
    failures = [e for e in events if e.type == "page_embedding_failed"]
    assert len(failures) == 1
    assert "embedder unavailable" in failures[0].data.get("error", "")


def test_rebuild_vectors_serialized_under_run_lock(tmp_path: Path) -> None:
    """F26 race fix: rebuild_vectors holds the run lock for its delete-and-
    replace of wiki_embeddings, so it cannot interleave with a commit's
    incremental embed (which also locks). Proven by it refusing to run while
    the lock is held."""
    import pytest

    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run
    from wikify.bundle.run.lock import LockHeldError, run_lock
    from wikify.bundle.wiki.derived import rebuild_vectors

    bdir = tmp_path / "bundle"
    (bdir / "run").mkdir(parents=True)
    bundle = Bundle(root=bdir)
    init_run(bundle, corpus_path="x")
    with run_lock(bundle, owner="someone-else"):
        with pytest.raises(LockHeldError):
            rebuild_vectors(bundle)


def test_commit_emits_page_committed_event(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    commit_page(bundle, slug=slug)
    events = read_events(bundle)
    types = [e.type for e in events]
    assert "page_committed" in types
    last = events[-1]
    assert last.page_id == "Atomic Layer Deposition"


def test_commit_rejects_when_validation_missing(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path="x")
    s, _ = create_concept(bundle, page_id="ALD")
    with pytest.raises(CommitGateError, match="draft.json"):
        commit_page(bundle, slug=s)


def test_commit_rejects_when_validation_failed(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    # Overwrite verdict to ok=false.
    verdict_p = validation_path(bundle, slug)
    verdict = read_json(verdict_p)
    verdict["ok"] = False
    write_json(verdict_p, verdict)
    with pytest.raises(CommitGateError, match="ok=false"):
        commit_page(bundle, slug=slug)


def test_commit_infers_links_from_existing_committed_pages(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    commit_page(bundle, slug=slug)

    mem_slug, _ = create_concept(bundle, page_id="Memristor")
    corpus = _make_corpus(tmp_path / "corpus2")
    append_evidence(
        bundle, mem_slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(
        bundle,
        slug=mem_slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
    )
    chunk_text = read_json(draft_path(bundle, mem_slug))["evidence"][0]["chunk_text"]
    quote = chunk_text[:30].strip()
    payload = _good_response_payload(quote)
    payload["page_id"] = "Memristor"
    payload["body_markdown"] = payload["body_markdown"].replace(
        "Atomic Layer Deposition", "Memristor"
    )
    payload["body_markdown"] = payload["body_markdown"].replace(
        "advanced semiconductor nodes [^e1].",
        "advanced semiconductor nodes and Atomic Layer Deposition workflows [^e1].",
    )
    write_json(response_path(bundle, mem_slug), payload)
    validate_response(bundle, mem_slug)

    result = commit_page(bundle, slug=mem_slug)

    text = result.page_path.read_text(encoding="utf-8")
    assert 'links: ["Atomic Layer Deposition"]' in text


def test_commit_links_with_commas_round_trip(tmp_path: Path) -> None:
    bundle, slug = _setup_validated(tmp_path)
    comma_quote = read_json(draft_path(bundle, slug))["evidence"][0]["chunk_text"][:30].strip()
    comma_payload = _good_response_payload(comma_quote)
    comma_payload["page_id"] = "Chua, Circuit Theory"
    write_json(response_path(bundle, slug), comma_payload)
    validate_response(bundle, slug)
    commit_page(bundle, slug=slug)

    mem_slug, _ = create_concept(bundle, page_id="Memristor")
    corpus = _make_corpus(tmp_path / "corpus3")
    append_evidence(
        bundle, mem_slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(
        bundle,
        slug=mem_slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
    )
    quote = read_json(draft_path(bundle, mem_slug))["evidence"][0]["chunk_text"][:30].strip()
    payload = _good_response_payload(quote)
    payload["page_id"] = "Memristor"
    payload["body_markdown"] = payload["body_markdown"].replace(
        "advanced semiconductor nodes [^e1].",
        "advanced semiconductor nodes and Chua, Circuit Theory [^e1].",
    )
    write_json(response_path(bundle, mem_slug), payload)
    validate_response(bundle, mem_slug)

    result = commit_page(bundle, slug=mem_slug)

    page = parse_page(result.page_path)
    assert page.links == ["Chua, Circuit Theory"]


def test_rebuild_index_lists_committed_pages(tmp_path: Path) -> None:
    from wikify.bundle.wiki.commit import rebuild_projections
    bundle, slug = _setup_validated(tmp_path)
    commit_page(bundle, slug=slug)
    rebuild_projections(bundle)
    payload = json.loads(bundle.derived_index_path.read_text(encoding="utf-8"))
    pages = payload["pages"]
    assert any(p["slug"] == slug for p in pages)
