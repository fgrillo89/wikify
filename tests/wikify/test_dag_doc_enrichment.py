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


def test_bib_title_overrides_stuck_doc_title(tmp_path: Path) -> None:
    """When ``corpus_papers.bib`` from a prior refresh has a clean title
    that disagrees with the current ``doc.title``, the bib value must
    win — even when the current title would otherwise pass every junk
    heuristic. This is the "title resolution always contends with bib"
    contract."""
    from wikify.ingest.bibtex import enrich_doc_metadata

    corpus = Corpus(root=tmp_path / "corpus")
    corpus.ensure()
    doc = Document(
        id="paper_under_test",
        # No underscores, no placeholder vocabulary, mixed case, > 10
        # chars: this current title would NOT be flagged by any of the
        # other junk heuristics. Only the bib disagreement triggers
        # re-evaluation.
        source_path="some-source.docx",
        kind="docx",
        title="Switchingdynamicsandcomputingapplicationsofmemristors",
        metadata={
            "title": "Switchingdynamicsandcomputingapplicationsofmemristors",
            "authors": ["Duan"],
            "year": 2017,
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
    _write_markdown(corpus, doc, "Some body without a heading.\n")

    bib_titles = {
        "paper_under_test": (
            "Switching dynamics and computing applications of memristors: An overview"
        ),
    }

    enriched = enrich_doc_metadata(
        corpus, doc,
        resolve_doi=False, doi_lookup=None,
        bib_titles=bib_titles,
    )

    assert enriched.title == (
        "Switching dynamics and computing applications of memristors: An overview"
    )
    assert enriched.metadata["title"] == enriched.title


def test_bib_title_does_not_override_clean_filename_title(tmp_path: Path) -> None:
    """When the filename gives a clean title, it must continue to win
    over the bib title — the bib is a strong adjacent candidate, not a
    blanket override of filename."""
    from wikify.ingest.bibtex import enrich_doc_metadata

    corpus = Corpus(root=tmp_path / "corpus")
    fn_title = "Memristor Architectures for Neuromorphic Computing"
    doc = _doc_with_junk_title(f"[2020 Smith] {fn_title}.docx")
    _write_markdown(corpus, doc, f"# {fn_title}\n\nBody.\n")

    bib_titles = {doc.id: "Some Less Authoritative Bib Title String"}

    enriched = enrich_doc_metadata(
        corpus, doc,
        resolve_doi=False, doi_lookup=None,
        bib_titles=bib_titles,
    )

    assert enriched.title == fn_title


def test_read_existing_bib_titles_returns_empty_when_no_bib(tmp_path: Path) -> None:
    """First-ingest path: no bib file yet -> empty dict, no error.

    Guards against the bib-loader being called eagerly on first ingest
    where ``corpus_papers.bib`` does not exist."""
    from wikify.ingest.bibtex import read_existing_bib_titles

    corpus = Corpus(root=tmp_path / "corpus")
    assert read_existing_bib_titles(corpus) == {}


def test_enrichment_self_heals_underscore_compressed_titles(tmp_path: Path) -> None:
    """A doc whose stored title is an underscore-compressed leftover (the
    historical broken state, e.g. ``Memristor-Themissingcircuit_element``)
    must be re-derived from the filename on the next refresh.

    Without this, corpora ingested before PR #62 stay stuck because the
    compressed string passes every other junk-detection rule (10+ chars,
    not all-caps, not in the placeholder vocabulary) and the enrichment
    early-outs."""
    corpus = Corpus(root=tmp_path / "corpus")
    doc = Document(
        id="paper_under_test",
        source_path="[1971 Chua] Memristor-The_missing_circuit_element.docx",
        kind="docx",
        # The historical broken state. Note: this string is non-empty,
        # 35 chars, mixed case, contains no [YYYY] prefix — every other
        # heuristic accepts it.
        title="Memristor-Themissingcircuit_element",
        metadata={
            "title": "Memristor-Themissingcircuit_element",
            "authors": ["Chua"],
            "year": 1971,
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
    _write_markdown(
        corpus, doc,
        # No body heading: forces the fallback to derive the title from
        # the filename, which is the exact path the original bug broke.
        "Some unrelated body text without a markdown heading.\n",
    )

    ctx = {"paths": corpus, "docs": [doc]}
    _refresh_doc_enrichment(ctx)

    enriched = ctx["docs"][0]
    # Healed: no surviving underscores.
    assert "_" not in enriched.title, enriched.title
    assert "missing" in enriched.title
    assert "circuit" in enriched.title


def test_enrichment_self_heals_concatenated_xmp_titles(tmp_path: Path) -> None:
    """Some source PDFs ship XMP/info titles with whitespace stripped
    (e.g. ``SwitchingdynamicsandcomputingapplicationsofmemristorsAnoverview``).
    These slip through every other heuristic — no underscores, no
    placeholder vocabulary, mixed case, normal length — but a single
    >30-char alphabetical run is the dead giveaway.

    Re-derivation must succeed via the filename path when the source's
    ``[YYYY Author] Title.ext`` filename uses underscores as word
    separators."""
    corpus = Corpus(root=tmp_path / "corpus")
    doc = Document(
        id="paper_under_test",
        source_path=(
            "[2017 Duan] Switching_dynamics_and_computing_applications"
            "_of_memristors_An_overview.pdf"
        ),
        kind="pdf",
        title="SwitchingdynamicsandcomputingapplicationsofmemristorsAnoverview",
        metadata={
            "title": (
                "SwitchingdynamicsandcomputingapplicationsofmemristorsAnoverview"
            ),
            "authors": ["Duan"],
            "year": 2017,
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
    _write_markdown(corpus, doc, "Some body without a heading.\n")

    ctx = {"paths": corpus, "docs": [doc]}
    _refresh_doc_enrichment(ctx)

    enriched = ctx["docs"][0]
    # No long unbroken alpha run survives.
    assert all(
        sum(1 for c in tok if c.isalpha()) <= 30
        for tok in enriched.title.split()
    ), enriched.title
    # Filename-derived words are present, with spaces.
    assert "Switching" in enriched.title
    assert "dynamics" in enriched.title
    assert "memristors" in enriched.title


def test_concatenated_detector_does_not_flag_clean_long_titles() -> None:
    """Real titles with proper word boundaries must not trip the
    concatenated detector — even when individual words are long
    chemistry compounds."""
    from wikify.ingest.bibtex import _title_needs_fallback

    clean_titles = [
        "Memristor-The missing circuit element",
        "The missing memristor found",
        "Atomic Layer Deposition of Polytetrafluoroethylene precursors",
        "Tetraethylorthosilicate as a precursor for thin-film growth",
        "Switching dynamics and computing applications of memristors: An overview",
    ]
    for title in clean_titles:
        assert not _title_needs_fallback(title), title
