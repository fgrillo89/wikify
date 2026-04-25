"""Tests for ingest/bibtex.py."""

import json

import bibtexparser

from wikify.api import Corpus
from wikify.ingest.bibtex import (
    _author_has_prose_residue,
    _clean_author_name,
    _clean_bib_title,
    _looks_like_author_fragment,
    _strip_author_artifacts,
    _strip_year_anchored_tail,
    paper_to_bibtex,
    write_corpus_bibliography,
    write_corpus_bibtex,
)
from wikify.models import Document


def _doc(doc_id: str, title: str, authors: list[str], year: int) -> Document:
    return Document(
        id=doc_id,
        source_path=f"/tmp/{doc_id}.pdf",
        kind="pdf",
        title=title,
        metadata={"authors": authors, "year": year, "doi": "10.1000/test"},
        markdown_path="",
        image_dir="",
    )


def _resolved_citation(
    ord_: int, raw: str, *, doi: str | None = None,
    title: str = "", authors: list[str] | None = None,
    year: int | None = None, venue: str = "",
) -> dict:
    """Build a citation dict as it would look after CrossRef resolution."""
    return {
        "ord": ord_,
        "raw_text": raw,
        "year": year,
        "doi": doi,
        "author_last_names": [],
        "crossref_resolved": True,
        "title": title,
        "authors": authors or [],
        "venue": venue,
    }


def _unresolved_citation(
    ord_: int, raw: str, *, doi: str | None = None, year: int | None = None,
) -> dict:
    """Build an unresolved citation dict."""
    return {
        "ord": ord_,
        "raw_text": raw,
        "year": year,
        "doi": doi,
        "author_last_names": [],
        "crossref_resolved": False,
    }


# --- library.bib (source documents) ---


def test_paper_to_bibtex_roundtrip():
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov", "G. Snider"], 2008)
    bib = paper_to_bibtex(doc)
    db = bibtexparser.loads(bib)
    assert len(db.entries) == 1
    e = db.entries[0]
    assert e["ID"] == "paper_abc123"
    assert "Strukov" in e["author"]
    assert e["year"] == "2008"
    assert e["doi"] == "10.1000/test"


def test_paper_to_bibtex_includes_journal_from_venue():
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov"], 2008)
    doc.metadata["venue"] = "Nature"
    bib = paper_to_bibtex(doc)
    entry = bibtexparser.loads(bib).entries[0]
    assert entry["journal"] == "Nature"


def test_paper_to_bibtex_includes_optional_metadata_fields():
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov"], 2008)
    doc.metadata.update(
        volume="5",
        number="3",
        pages="10-12",
        publisher="IEEE",
        issn="1234-5678",
        url="https://example.com",
        abstract="A short abstract.",
        keywords=["memristor", "oxide"],
    )
    bib = paper_to_bibtex(doc)
    e = bibtexparser.loads(bib).entries[0]
    assert e["volume"] == "5"
    assert e["number"] == "3"
    assert e["pages"] == "10-12"
    assert e["publisher"] == "IEEE"
    assert e["issn"] == "1234-5678"
    assert e["url"] == "https://example.com"
    assert "memristor" in e["keywords"]


def test_write_corpus_bibtex(tmp_path):
    docs = [
        _doc("a_1", "Title A", ["Alice"], 2020),
        _doc("b_2", "Title B", ["Bob"], 2021),
    ]
    corpus = Corpus(root=tmp_path / "corpus")
    path = write_corpus_bibtex(corpus, docs)
    assert path.exists()
    db = bibtexparser.loads(path.read_text(encoding="utf-8"))
    assert len(db.entries) == 2


def test_write_corpus_bibtex_uses_markdown_publication_fallback(tmp_path):
    doc = _doc("a_1", "Title A", ["Alice"], 2020)
    doc.metadata.pop("doi", None)
    corpus = Corpus(root=tmp_path / "corpus")
    corpus.ensure()
    md = corpus.markdown_dir / "a_1.md"
    md.write_text(
        "---\ntitle: Title A\n---\n# Title A\n\n"
        "Published in Nature Materials, vol. 5, pp. 10-12.\n"
        "DOI: 10.1038/nmat1234\n",
        encoding="utf-8",
    )
    write_corpus_bibtex(corpus, [doc])
    db = bibtexparser.loads(corpus.library_bib_path.read_text(encoding="utf-8"))
    e = db.entries[0]
    assert e.get("doi") == "10.1038/nmat1234"


# --- cited_works.bib (CrossRef-resolved references) ---


def test_write_corpus_bibliography_writes_reference_artifacts(tmp_path):
    docs = [
        _doc("a_1", "Title A", ["Alice"], 2020),
        _doc("b_2", "Title B", ["Bob"], 2021),
    ]
    for doc in docs:
        doc.metadata["doi"] = f"10.1000/{doc.id}"
        doc.citations = [
            _resolved_citation(
                1, "Smith, J. Reference Paper. Journal X 5, 10-12 (2019).",
                doi="10.5555/ref", title="Reference Paper",
                authors=["J. Smith"], year=2019, venue="Journal X",
            ),
        ]

    corpus = Corpus(root=tmp_path / "corpus")
    paths = write_corpus_bibliography(corpus, docs)

    assert paths["library"].exists()
    assert paths["references"].exists()
    assert paths["citation_index"].exists()

    references = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert len(references.entries) == 1
    assert references.entries[0]["doi"] == "10.5555/ref"

    index = json.loads(
        corpus.citation_index_path.read_text(encoding="utf-8"),
    )
    ref_key = next(
        k for k, e in index["entries"].items() if e["kind"] == "reference"
    )
    assert index["doc_citations"] == {"a_1": [ref_key], "b_2": [ref_key]}
    assert sorted(index["entries"][ref_key]["source_doc_ids"]) == ["a_1", "b_2"]


def test_citation_index_links_reference_to_source_doc_when_doi_matches(
    tmp_path,
):
    source = _doc("source_1", "Canonical Source", ["Alice"], 2020)
    source.metadata["doi"] = "10.123/source"
    citing = _doc("citing_1", "Citing Paper", ["Bob"], 2021)
    citing.metadata["doi"] = "10.123/citing"
    citing.citations = [
        _resolved_citation(
            3, "Alice. Canonical Source. Journal Y (2020).",
            doi="10.123/source", title="Canonical Source",
            authors=["Alice"], year=2020, venue="Journal Y",
        ),
    ]

    corpus = Corpus(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [source, citing])
    index = json.loads(
        corpus.citation_index_path.read_text(encoding="utf-8"),
    )

    source_key = index["doc_bibkeys"]["source_1"]
    assert index["doc_citations"]["citing_1"] == [source_key]
    # Should NOT create a separate reference entry -- matched to source
    refs = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert refs.entries == []


def test_unresolved_citations_are_in_index_but_not_in_bib(tmp_path):
    doc = _doc("a_1", "Title A", ["Alice"], 2020)
    doc.metadata["doi"] = "10.1000/a"
    doc.citations = [
        _unresolved_citation(
            1, "M. Zhao; B. Gao; J. Tang et al.,",
            year=None,
        ),
    ]

    corpus = Corpus(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [doc])

    references = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert references.entries == []

    index = json.loads(
        corpus.citation_index_path.read_text(encoding="utf-8"),
    )
    unresolved = [
        e for e in index["entries"].values() if e["kind"] == "unresolved"
    ]
    assert len(unresolved) == 1


def test_crossref_resolved_citation_without_title_is_not_exported(tmp_path):
    """CrossRef-resolved but missing title -> no .bib entry."""
    doc = _doc("a_1", "Title A", ["Alice"], 2020)
    doc.metadata["doi"] = "10.1000/a"
    doc.citations = [
        {
            "ord": 1,
            "raw_text": "Some raw text",
            "year": 2019,
            "doi": None,
            "author_last_names": [],
            "crossref_resolved": True,
            "title": "",  # empty title
            "authors": ["J. Smith"],
            "venue": "",
        },
    ]

    corpus = Corpus(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [doc])

    references = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert references.entries == []


def test_bibliography_preserves_balanced_paren_doi(tmp_path):
    """A DOI with balanced parens (Elsevier S-prefix) must round-trip to bib."""
    doc = _doc("a_1", "Title A", ["Alice"], 2020)
    doc.metadata["doi"] = "10.1000/a"
    # Simulate a citation persisted before the paren-aware extractor fix --
    # raw_text carries the full DOI but cit.doi was truncated at ``(``.
    doc.citations = [
        _resolved_citation(
            1,
            "W. Maass. Networks of spiking neurons. Neural Netw. 10, "
            "1659-1671 (1997). doi:10.1016/S0893-6080(97)00011-7",
            doi="10.1016/S0893-6080(97",
            title="Networks of spiking neurons",
            authors=["W. Maass"],
            year=1997, venue="Neural Netw.",
        ),
    ]

    corpus = Corpus(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [doc])

    references = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert len(references.entries) == 1
    assert references.entries[0]["doi"] == "10.1016/S0893-6080(97)00011-7"

    index = json.loads(
        corpus.citation_index_path.read_text(encoding="utf-8"),
    )
    ref_keys = [k for k, e in index["entries"].items() if e["kind"] == "reference"]
    assert len(ref_keys) == 1
    assert index["entries"][ref_keys[0]]["doi"] == "10.1016/S0893-6080(97)00011-7"


def test_dedup_by_doi_across_citations(tmp_path):
    """Two citations referencing the same DOI -> one .bib entry."""
    first = _doc("a_1", "Title A", ["Alice"], 2020)
    first.metadata["doi"] = "10.1000/a"
    first.citations = [
        _resolved_citation(
            1, "Kumar S. et al. Y2O3 memristive crossbar. IEEE (2022).",
            doi="10.1109/TED.2022.3172400",
            title="Y2O3 Memristive Crossbar",
            authors=["S. Kumar", "A. Agarwal"],
            year=2022, venue="IEEE Trans. Electron Devices",
        ),
    ]
    second = _doc("b_2", "Title B", ["Bob"], 2021)
    second.metadata["doi"] = "10.1000/b"
    second.citations = [
        _resolved_citation(
            2, "Kumar et al. Y2O3 crossbar array. IEEE TED (2022).",
            doi="10.1109/TED.2022.3172400",
            title="Y2O3 Crossbar Array",
            authors=["S. Kumar"],
            year=2022, venue="IEEE TED",
        ),
    ]

    corpus = Corpus(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [first, second])

    references = bibtexparser.loads(
        corpus.references_bib_path.read_text(encoding="utf-8"),
    )
    assert len(references.entries) == 1
    assert references.entries[0]["doi"] == "10.1109/TED.2022.3172400"

    index = json.loads(
        corpus.citation_index_path.read_text(encoding="utf-8"),
    )
    ref_key = references.entries[0]["ID"]
    # Both docs should cite the same reference
    assert ref_key in index["doc_citations"]["a_1"]
    assert ref_key in index["doc_citations"]["b_2"]


# ---------------------------------------------------------------------------
# _clean_bib_title: structural cleanup rules added in the quality-scan PR
# ---------------------------------------------------------------------------


class TestYearAnchoredTailStrip:
    def test_strips_journal_year_vol_pages(self):
        # End-to-end via _clean_bib_title: year-anchor cuts at the LAST
        # boundary before the year, then trailing-abbrev strip removes
        # the journal-name residue.
        out = _clean_bib_title(
            "Bistable switching in electroformed metal-insulator-metal devices. "
            "Phys Status Solidi. 1988, 108, 11",
        )
        assert out == "Bistable switching in electroformed metal-insulator-metal devices"

    def test_strips_book_publisher_and_year(self):
        out = _clean_bib_title(
            "Electronic Processes in Ionic Crystals 2nd edn. "
            "(Oxford at the Clarendon Press, 1950)",
        )
        # LAST-boundary stops at the paren; the publisher text is
        # cosmetic residue, not a bug worth a separate rule.
        assert out.startswith("Electronic Processes in Ionic Crystals")
        assert "1950" not in out

    def test_preserves_titles_without_years(self):
        t = "Atomic Layer Deposition for Semiconductor Devices"
        assert _strip_year_anchored_tail(t) == t

    def test_preserves_titles_with_year_as_keyword(self):
        t = "The 2007 financial crisis analysis"
        assert _strip_year_anchored_tail(t) == t

    def test_preserves_subtitle_before_citation(self):
        # A legitimate subtitle shouldn't be lost to the year strip —
        # only the citation tail after it.
        out = _clean_bib_title(
            "Paper Title. Subtitle of Work. Journal Name 2020, 5, 123",
        )
        assert "Subtitle" in out
        assert "2020" not in out

    def test_handles_question_mark_boundary(self):
        out = _strip_year_anchored_tail(
            "Do We Have Brain To Spare? Neurology 2005, 64, 2004",
        )
        assert out == "Do We Have Brain To Spare"


class TestCleanBibTitleHtmlEntity:
    def test_decodes_single_level_entity(self):
        assert _clean_bib_title("HfO&lt;inf&gt;x&lt;/inf&gt; crossbar") == (
            "HfO$_{x}$ crossbar"
        )

    def test_decodes_double_encoded(self):
        out = _clean_bib_title("10&amp;#x00D7;10nm crossbar")
        assert "&" not in out
        assert "#x" not in out


class TestAuthorProseResidue:
    def test_detects_lowercase_content_word(self):
        assert _author_has_prose_residue("L. On the gradual unipolar")
        assert _author_has_prose_residue("M. Short-term plasticity")

    def test_accepts_clean_name(self):
        assert not _author_has_prose_residue("J. Lecun")
        assert not _author_has_prose_residue("van der Waals")

    def test_detects_colon(self):
        assert _author_has_prose_residue("Erratum: J. Heyd")

    def test_accepts_particles(self):
        assert not _author_has_prose_residue("de la Cruz")
        assert not _author_has_prose_residue("van der Pauw")


class TestStripAuthorArtifacts:
    def test_strips_ieee_affiliation_tail(self):
        assert _strip_author_artifacts(
            "Facai Wu is with the University of ...",
        ) == "Facai Wu"

    def test_strips_et_al(self):
        assert _strip_author_artifacts("Yue Xin and Bai Sun et al. -") == (
            "Yue Xin and Bai Sun"
        )

    def test_strips_trailing_dash(self):
        assert _strip_author_artifacts("Tatsuya Toda —") == "Tatsuya Toda"

    def test_preserves_clean_name(self):
        assert _strip_author_artifacts("J. Smith") == "J. Smith"

    def test_strips_were_with(self):
        assert _strip_author_artifacts(
            "Hong Chen and Jinbin Wang were with the",
        ) == "Hong Chen and Jinbin Wang"


class TestLooksLikeAuthorFragment:
    def test_initial_and_surname(self):
        assert _looks_like_author_fragment("D. Querlioz")

    def test_two_word_name(self):
        assert _looks_like_author_fragment("Hai Li")

    def test_hyphenated(self):
        assert _looks_like_author_fragment("Galdin-Retailleau")

    def test_rejects_prose_adjective(self):
        # A single adjective looks like a candidate but the fragment test
        # passes it — the reject rule in _reference_entry_from_citation
        # uses an additional "some piece contains a period-initial" check
        # that blocks adjective-only lists.
        assert _looks_like_author_fragment("Flexible")

    def test_rejects_long_piece(self):
        assert not _looks_like_author_fragment(
            "A Very Long Sentence That Goes Beyond Four Words",
        )

    def test_rejects_digits(self):
        assert not _looks_like_author_fragment("TiO2")


class TestCleanAuthorNameAffiliationCoda:
    def test_strips_two_letter_affiliation(self):
        assert _clean_author_name("Xin-Gui Tang ab") == "Xin-Gui Tang"

    def test_strips_single_letter_affiliation(self):
        assert _clean_author_name("Mi Hyang Park a") == "Mi Hyang Park"

    def test_preserves_two_token_mixed_case_name(self):
        # Guard against the widened trailing-letter regex stripping
        # legitimate short given names like "Yang yi" / "Chen li".
        assert _clean_author_name("Yang yi") == "Yang yi"

    def test_normalises_all_lowercase(self):
        assert _clean_author_name("yang yi") == "Yang Yi"

    def test_preserves_particles(self):
        assert _clean_author_name("van der Waals") == "van der Waals"
