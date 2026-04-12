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
        {
            "doi": "10.1038/nature06932]",
            "venue": "Nature",
            "volume": "453",
            "issue": "7191",
            "pages": "80-83",
            "abstract": "A short abstract.",
            "keywords": ["memristor", "nanoelectronics"],
        }
    )
    bib = paper_to_bibtex(doc)
    entry = bibtexparser.loads(bib).entries[0]
    assert entry["doi"] == "10.1038/nature06932"
    assert entry["url"] == "https://doi.org/10.1038/nature06932"
    assert entry["journal"] == "Nature"
    assert entry["volume"] == "453"
    assert entry["number"] == "7191"
    assert entry["pages"] == "80-83"
    assert entry["abstract"] == "A short abstract."
    assert entry["keywords"] == "memristor, nanoelectronics"


def test_write_corpus_bibtex(tmp_path):
    docs = [
        _doc("a_1", "Title A", ["Alice"], 2020),
        _doc("b_2", "Title B", ["Bob"], 2021),
        _doc("c_3", "Title C", ["Carol"], 2022),
    ]
    corpus = CorpusPaths(root=tmp_path / "corpus")
    bib_path = write_corpus_bibtex(corpus, docs)
    assert bib_path.exists()
    db = bibtexparser.loads(bib_path.read_text(encoding="utf-8"))
    assert len(db.entries) == 3


def test_write_corpus_bibtex_uses_markdown_publication_fallback(tmp_path):
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov"], 2008)
    doc.metadata["doi"] = None
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.ensure()
    (corpus.markdown_dir / f"{doc.id}.md").write_text(
        "# Paper\n\n"
        "doi:10.1038/nature06932]\n\n"
        "Body.\n\n"
        "## References\n\n"
        "1. Reference that should not win. Science 1, 1 (1999).\n\n"
        "## Nature 453, 80-83 (2008)\n\n",
        encoding="utf-8",
    )

    bib_path = write_corpus_bibtex(corpus, [doc])
    entry = bibtexparser.loads(bib_path.read_text(encoding="utf-8")).entries[0]
    assert entry["journal"] == "Nature"
    assert entry["volume"] == "453"
    assert entry["pages"] == "80-83"
    assert entry["doi"] == "10.1038/nature06932"


def test_write_corpus_bibtex_ignores_reference_doi(tmp_path):
    doc = _doc("paper_abc123", "On Memristors", ["D. Strukov"], 2008)
    doc.metadata["doi"] = None
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.ensure()
    (corpus.markdown_dir / f"{doc.id}.md").write_text(
        "# Paper\n\nBody.\n\n## References\n\n1. Reference doi:10.1063/5.0093964",
        encoding="utf-8",
    )

    bib_path = write_corpus_bibtex(corpus, [doc])
    entry = bibtexparser.loads(bib_path.read_text(encoding="utf-8")).entries[0]
    assert "doi" not in entry


def test_write_corpus_bibtex_uses_markdown_author_fallback(tmp_path):
    doc = _doc("paper_abc123", "On Memristors", ["Strukov"], 2008)
    doc.source_path = "/tmp/[2008 Strukov] On Memristors.pdf"
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.ensure()
    (corpus.markdown_dir / f"{doc.id}.md").write_text(
        "# On Memristors\n\nD. Strukov, G. Snider, D. Stewart, and R. Williams\n\nAbstract.",
        encoding="utf-8",
    )

    bib_path = write_corpus_bibtex(corpus, [doc])
    entry = bibtexparser.loads(bib_path.read_text(encoding="utf-8")).entries[0]
    assert entry["author"] == "D. Strukov and G. Snider and D. Stewart and R. Williams"


def test_write_corpus_bibtex_can_merge_generic_doi_metadata(tmp_path):
    doc = _doc("paper_abc123", "On Memristors", ["Strukov"], 2008)
    doc.metadata["doi"] = "10.1000/test"
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.ensure()

    def lookup(doi: str) -> dict[str, object]:
        assert doi == "10.1000/test"
        return {
            "venue": "Journal of Memristors",
            "volume": "12",
            "pages": "34-56",
            "authors": ["D. Strukov", "G. Snider"],
        }

    bib_path = write_corpus_bibtex(corpus, [doc], resolve_doi=True, doi_lookup=lookup)
    entry = bibtexparser.loads(bib_path.read_text(encoding="utf-8")).entries[0]
    assert entry["journal"] == "Journal of Memristors"
    assert entry["volume"] == "12"
    assert entry["pages"] == "34-56"
    assert entry["author"] == "D. Strukov and G. Snider"


def test_write_corpus_bibtex_repairs_docling_like_metadata_from_markdown(tmp_path):
    doc = _doc(
        "paper_abc123",
        "[2015 Matveyev] Resistive switching and synaptic properties of fully "
        "atomic layer deposition grown TiNHfO2",
        ["Matveyev"],
        2015,
    )
    doc.metadata["doi"] = "10.1063/1.4905792&domain=pdf&date_stamp=2015-01-26"
    doc.metadata["venue"] = "Cite as: J. Appl. Phys."
    corpus = CorpusPaths(root=tmp_path / "corpus")
    corpus.ensure()
    (corpus.markdown_dir / f"{doc.id}.md").write_text(
        "---\n"
        "title: filename fallback\n"
        "---\n\n"
        "## Resistive switching and synaptic properties of fully atomic layer "
        "deposition grown TiN/HfO2/TiN devices \ue907\n\n"
        "[Yu. Matveyev ; K. Egorov; A. Markeev; A. Zenkevich](javascript:;)\n\n"
        "J. Appl. Phys. 117, 044901 (2015)\n\n"
        "[https://doi.org/10.1063/1.4905792](https://doi.org/10.1063/1.4905792)\n",
        encoding="utf-8",
    )

    bib_path = write_corpus_bibtex(corpus, [doc])
    entry = bibtexparser.loads(bib_path.read_text(encoding="utf-8")).entries[0]
    assert entry["title"] == (
        "Resistive switching and synaptic properties of fully atomic layer deposition "
        "grown TiN/HfO2/TiN devices"
    )
    assert entry["author"] == "Yu. Matveyev and K. Egorov and A. Markeev and A. Zenkevich"
    assert entry["doi"] == "10.1063/1.4905792"
    assert entry["journal"] == "J. Appl. Phys."


def test_write_corpus_bibliography_writes_reference_artifacts(tmp_path):
    docs = [
        _doc("a_1", "Title A", ["Alice"], 2020),
        _doc("b_2", "Title B", ["Bob"], 2021),
    ]
    for doc in docs:
        doc.metadata["doi"] = f"10.1000/{doc.id}"
        doc.citations = [
            {
                "ord": 1,
                "raw_text": "Smith, J. Reference Paper. Journal X 5, 10-12 (2019).",
                "authors": ["J. Smith"],
                "year": "2019",
                "title": "Reference Paper",
                "venue": "Journal X",
                "doi": "10.5555/ref",
            }
        ]

    corpus = CorpusPaths(root=tmp_path / "corpus")
    paths = write_corpus_bibliography(corpus, docs)

    assert paths["library"].exists()
    assert paths["references"].exists()
    assert paths["bibliography"].exists()
    assert paths["citation_index"].exists()

    references = bibtexparser.loads(corpus.references_bib_path.read_text(encoding="utf-8"))
    assert len(references.entries) == 1
    assert references.entries[0]["doi"] == "10.5555/ref"

    index = json.loads(corpus.citation_index_path.read_text(encoding="utf-8"))
    ref_key = next(k for k, e in index["entries"].items() if e["kind"] == "reference")
    assert index["doc_citations"] == {"a_1": [ref_key], "b_2": [ref_key]}
    assert index["entries"][ref_key]["source_doc_ids"] == ["a_1", "b_2"]


def test_citation_index_links_reference_to_source_doc_when_doi_matches(tmp_path):
    source = _doc("source_1", "Canonical Source", ["Alice"], 2020)
    source.metadata["doi"] = "10.123/source"
    citing = _doc("citing_1", "Citing Paper", ["Bob"], 2021)
    citing.metadata["doi"] = "10.123/citing"
    citing.citations = [
        {
            "ord": 3,
            "raw_text": "Alice. Canonical Source. Journal Y (2020).",
            "authors": ["Alice"],
            "year": "2020",
            "title": "Canonical Source",
            "venue": "Journal Y",
            "doi": "10.123/source",
        }
    ]

    corpus = CorpusPaths(root=tmp_path / "corpus")
    write_corpus_bibliography(corpus, [source, citing])
    index = json.loads(corpus.citation_index_path.read_text(encoding="utf-8"))

    source_key = index["doc_bibkeys"]["source_1"]
    assert index["doc_citations"]["citing_1"] == [source_key]
    assert not bibtexparser.loads(corpus.references_bib_path.read_text(encoding="utf-8")).entries
    assert index["entries"][source_key]["source_doc_ids"] == ["citing_1"]
