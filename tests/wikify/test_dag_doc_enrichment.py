"""Tests for the DAG-level ``doc_enrichment`` step.

The historical bug: title fallback (``"Word Document"`` placeholder ->
filename-derived real title) lived inside ``write_corpus_bibliography``
as a *local* enrichment used only to build the bibtex output. The
enriched docs were never written back to ``ctx["docs"]``, so
``build_knowledge_graph`` (Wave E) and ``_resave_docs`` (Wave F)
silently kept the placeholder title in ``knowledge_graph.json`` and in
the persisted ``docs/<id>.json`` records — even though
``corpus_papers.bib`` looked correct.

These tests guard the fix: a dedicated DAG step
(``_refresh_doc_enrichment``) runs before bibliography and replaces
``ctx["docs"]`` with the enriched list, so every downstream wave sees
the cleaned metadata.
"""

from __future__ import annotations

from pathlib import Path

from wikify.api import Corpus
from wikify.ingest.dag import REFRESH_DAG, _refresh_doc_enrichment
from wikify.models import Document


def _doc_with_junk_title(source_filename: str) -> Document:
    """Build a Document whose title is the ``"Word Document"`` placeholder."""
    return Document(
        id="paper_under_test",
        source_path=source_filename,
        kind="docx",
        title="Word Document",
        metadata={
            "title": "Word Document",
            "authors": ["Smith"],
            "year": 2020,
        },
        markdown_path="markdown/paper_under_test.md",
        image_dir="images/paper_under_test/",
        sections=[],
        images=[],
        equations=[],
        cites=[],
        n_chunks=1,
        n_tokens=10,
    )


def _write_markdown(corpus: Corpus, doc: Document, body: str) -> None:
    corpus.ensure()
    md_path = corpus.markdown_dir / f"{doc.id}.md"
    md_path.write_text(body, encoding="utf-8")


def test_refresh_doc_enrichment_replaces_junk_title_from_filename(
    tmp_path: Path,
) -> None:
    """Filename-first priority: ``[YYYY Author] Real Title.docx`` is
    authoritative, so the enrichment must overwrite ``"Word Document"``."""
    corpus = Corpus(root=tmp_path / "corpus")
    doc = _doc_with_junk_title(
        "[2020 Smith] Memristor Architectures for Neuromorphic Computing.docx",
    )
    _write_markdown(
        corpus, doc,
        "# Memristor Architectures for Neuromorphic Computing\n\nBody.\n",
    )

    ctx = {"paths": corpus, "docs": [doc]}
    _refresh_doc_enrichment(ctx)

    assert ctx["docs"] is not None
    assert len(ctx["docs"]) == 1
    enriched = ctx["docs"][0]
    assert enriched.title != "Word Document"
    assert "Memristor" in enriched.title


def test_refresh_doc_enrichment_replaces_ctx_docs_in_place(tmp_path: Path) -> None:
    """``ctx["docs"]`` must point at the enriched list afterwards (so
    Wave E / Wave F see the cleaned data)."""
    corpus = Corpus(root=tmp_path / "corpus")
    doc = _doc_with_junk_title(
        "[2020 Smith] Some Real Paper Title About Stuff.docx",
    )
    _write_markdown(
        corpus, doc,
        "# Some Real Paper Title About Stuff\n\nBody.\n",
    )

    ctx = {"paths": corpus, "docs": [doc]}
    original_list = ctx["docs"]
    _refresh_doc_enrichment(ctx)

    # The list reference is replaced (not mutated in-place); the new
    # list contains a freshly-constructed Document with the cleaned title.
    assert ctx["docs"] is not original_list
    assert ctx["docs"][0].title != "Word Document"


def test_refresh_doc_enrichment_idempotent_on_clean_title(tmp_path: Path) -> None:
    """Already-clean docs must round-trip unchanged so re-running refresh
    is safe and doesn't perturb correct titles."""
    corpus = Corpus(root=tmp_path / "corpus")
    clean_title = "An Already Reasonable Paper Title About Things"
    doc = Document(
        id="clean_doc",
        source_path=f"[2020 Smith] {clean_title}.docx",
        kind="docx",
        title=clean_title,
        metadata={
            "title": clean_title,
            "authors": ["Smith"],
            "year": 2020,
            "doi": "",
        },
        markdown_path="markdown/clean_doc.md",
        image_dir="images/clean_doc/",
        sections=[],
        images=[],
        equations=[],
        cites=[],
        n_chunks=1,
        n_tokens=10,
    )
    _write_markdown(corpus, doc, f"# {clean_title}\n\nBody.\n")

    ctx = {"paths": corpus, "docs": [doc]}
    _refresh_doc_enrichment(ctx)
    assert ctx["docs"][0].title == clean_title


def test_enrich_doc_metadata_returns_doc_with_clean_title(tmp_path: Path) -> None:
    """Regression test for the sibling bug in ``enrich_doc_metadata``
    itself: it correctly picked the cleaned title from the filename but
    then a downstream "sync metadata.title -> title" line silently
    pulled the still-junk metadata.title back over the cleanup. Fixed by
    guarding the sync against junk titles + keeping metadata.title in
    sync when the filename path supplies the cleaner value."""
    from wikify.ingest.bibtex import enrich_doc_metadata

    corpus = Corpus(root=tmp_path / "corpus")
    doc = _doc_with_junk_title(
        "[2020 Smith] Conductive Filament Dynamics in HfO2 Memristors.docx",
    )
    _write_markdown(
        corpus, doc,
        "# Conductive Filament Dynamics in HfO2 Memristors\n\nBody.\n",
    )

    enriched = enrich_doc_metadata(
        corpus, doc, resolve_doi=False, doi_lookup=None,
    )

    assert enriched.title != "Word Document"
    assert "Conductive Filament" in enriched.title
    # And the metadata-side title is in sync (downstream JSON sidecars
    # serialise both fields).
    assert enriched.metadata.get("title") == enriched.title


def test_doc_enrichment_step_runs_before_bibliography_in_refresh_dag() -> None:
    """Architectural guard: the enrichment must precede bibliography /
    knowledge_graph / doc_resave in the DAG, otherwise downstream waves
    consume un-enriched docs."""
    step_order: list[str] = []
    for wave in REFRESH_DAG:
        for step in wave.steps:
            step_order.append(step.name)

    assert "doc_enrichment" in step_order, (
        "doc_enrichment step is missing from REFRESH_DAG"
    )
    enrichment_idx = step_order.index("doc_enrichment")
    for downstream in ("bibliography", "knowledge_graph", "doc_resave"):
        assert downstream in step_order
        assert step_order.index(downstream) > enrichment_idx, (
            f"{downstream!r} must run after doc_enrichment, but DAG has "
            f"order: {step_order}"
        )
