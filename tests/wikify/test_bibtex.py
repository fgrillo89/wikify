"""Tests for ingest/bibtex.py."""

import json

import bibtexparser

from wikify.ingest.bibtex import (
    paper_to_bibtex,
    write_corpus_bibliography,
    write_corpus_bibtex,
)
from wikify.models import Document
from wikify.paths import CorpusPaths


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
    corpus = CorpusPaths(root=tmp_path / "corpus")
    path = write_corpus_bibtex(corpus, docs)
    assert path.exists()
    db = bibtexparser.loads(path.read_text(encoding="utf-8"))
    assert len(db.entries) == 2


def test_write_corpus_bibtex_uses_markdown_publication_fallback(tmp_path):
    doc = _doc("a_1", "Title A", ["Alice"], 2020)
    doc.metadata.pop("doi", None)
    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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

    corpus = CorpusPaths(root=tmp_path / "corpus")
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
