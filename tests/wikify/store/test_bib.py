"""Bib re-resolution + chunk_citations + bibtex export tests."""

from __future__ import annotations

from wikify.corpus.store import Store
from wikify.models import Chunk, Document


def _doc(doc_id, **meta) -> Document:
    return Document(
        id=doc_id, source_path=f"/p/{doc_id}.pdf", kind="pdf",
        title=meta.pop("title", f"Title of {doc_id}"),
        metadata=dict(authors=meta.pop("authors", ["Jane Doe"]),
                      year=meta.pop("year", 2024),
                      doi=meta.pop("doi", None)),
        markdown_path=f"corpus/markdown/{doc_id}.md",
        image_dir=f"corpus/images/{doc_id}/",
    )


def test_inbound_resolution_by_doi_creates_references_edge():
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc("d2", doi="https://doi.org/10.2/q"))
    # d1 cites a target via DOI; d2 not yet ingested, so it stays unresolved.
    s.upsert_bib_entries("d1", [
        {"title": "Sometarget", "year": 2023, "doi": "10.2/q", "raw_text": "ref a"},
    ])
    s.refresh_reference_edges("d1")
    # before re-resolve: no references edges, target_doc_id is NULL.
    assert s.con.execute(
        "SELECT target_doc_id FROM bib_entries WHERE doc_id='d1'",
    ).fetchone()[0] is None

    # inbound: d2 already exists with that DOI; re-resolve picks it up.
    n = s.reresolve_inbound("d2")
    assert n == 1
    targets = s.con.execute(
        "SELECT target_doc_id FROM bib_entries WHERE doc_id='d1'",
    ).fetchall()
    assert targets[0][0] == "d2"
    edges = [tuple(r) for r in s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges "
        "WHERE kind='references' AND src_type='document'",
    )]
    assert ("d1", "d2") in edges


def test_inbound_resolution_by_title_year():
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc("d2", title="A specific paper title with enough length", year=2022))
    s.upsert_bib_entries("d1", [
        {"title": "A specific paper title with enough length", "year": 2022, "raw_text": "x"},
    ])
    s.refresh_reference_edges("d1")
    assert s.reresolve_inbound("d2") == 1
    edges = [tuple(r) for r in s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='references'",
    )]
    assert ("d1", "d2") in edges


def test_inbound_resolution_alnum_normalisation():
    """Em-dashes, smart quotes, marker line-breaks, NFKD subscripts must
    not block a bib.title vs doc.title match. The resolver should treat
    these as the same canonical key."""
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    # Doc title has line-break artefact from PDF extraction (Marker output).
    s.upsert_document(_doc(
        "d2", title="Improving linearity by introducing Al in HfO\n2 as a memristor synapse device",
        year=2022,
    ))
    s.upsert_bib_entries("d1", [
        # Bib has the same title without the line break.
        {"title": "Improving linearity by introducing Al in HfO2 as a memristor synapse device",
         "year": 2022, "raw_text": "x"},
    ])
    assert s.reresolve_inbound("d2") == 1


def test_inbound_resolution_em_dash_vs_hyphen():
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc("d2", title="Memristor-The missing circuit element", year=1971))
    s.upsert_bib_entries("d1", [
        {"title": "Memristor—The missing circuit element", "year": 1971, "raw_text": "x"},
    ])
    assert s.reresolve_inbound("d2") == 1


def test_inbound_resolution_rawtext_fallback_for_null_title():
    """A bib with no extracted title should still resolve when the cited
    paper's title prefix appears in the bib raw_text and the year matches."""
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc(
        "d2",
        title="Atomic layer deposited HfZrO based flexible memristor for synapses",
        year=2023,
    ))
    s.upsert_bib_entries("d1", [
        # title=None; the doc title appears verbatim inside the raw_text.
        {"title": None, "year": 2023,
         "raw_text": "X. Author et al. Atomic layer deposited HfZrO based flexible "
                     "memristor for synapses. J. Mater. Chem. C 11 (2023) 1234."},
    ])
    n = s.reresolve_inbound("d2")
    assert n == 1
    res = s.con.execute(
        "SELECT resolution FROM bib_entries WHERE doc_id='d1'"
    ).fetchone()[0]
    assert res == "rawtext_year"


def test_inbound_resolution_rawtext_requires_year_match():
    """Raw-text fallback must not match across year boundaries."""
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc(
        "d2", title="Atomic layer deposited HfZrO based flexible memristor for synapses",
        year=2023,
    ))
    s.upsert_bib_entries("d1", [
        {"title": None, "year": 2019,  # wrong year
         "raw_text": "Atomic layer deposited HfZrO based flexible memristor for synapses. 2019."},
    ])
    assert s.reresolve_inbound("d2") == 0


def test_inbound_resolution_skips_short_doc_titles():
    """Avoid matching on stub doc titles ('Article', 'Editorial', etc.)
    which would generate huge false-positive sets."""
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_document(_doc("d2", title="Editorial", year=2022))
    s.upsert_bib_entries("d1", [
        {"title": "Editorial", "year": 2022, "raw_text": "Editorial 2022"},
    ])
    assert s.reresolve_inbound("d2") == 0


def test_chunk_citations_create_cites_edges():
    s = Store(":memory:")
    s.upsert_document(_doc("d1"))
    s.upsert_chunks([Chunk(id="d1/c0", doc_id="d1", ord=0, text="t",
                           char_span=(0, 1), section_path=[])])
    s.upsert_bib_entries("d1", [{"raw_text": "ref a", "title": "T", "year": 2023}])
    bibs = s.get_bib_entries("d1")
    bib_id = bibs[0]["bib_id"]
    s.upsert_chunk_citations("d1", [
        {
            "chunk_id": "d1/c0", "bib_id": bib_id,
            "marker_text": "[1]", "char_start": 5, "char_end": 8,
        },
    ])
    edges = [tuple(r) for r in s.con.execute(
        "SELECT src_id, dst_id FROM graph_edges WHERE kind='cites'",
    )]
    assert ("d1/c0", bib_id) in edges


def test_bibtex_export_corpus_and_cited():
    s = Store(":memory:")
    s.upsert_document(_doc("d1", year=2024, doi="10.1/x"))
    s.upsert_bib_entries("d1", [
        {"title": "Cited work", "year": 2020, "authors": ["A. Author"]},
    ])
    corpus_bib = s.export_bibtex("corpus")
    assert "@article{" in corpus_bib
    assert "Title of d1" in corpus_bib
    cited_bib = s.export_bibtex("cited")
    assert "Cited work" in cited_bib
