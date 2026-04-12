"""Structural quality guards for the ingest pipeline.

- sections populated on Document from parsed.sections through pipeline
- year extractors return None on miss (no datetime.now fallback)
- in-document authors win over a thin filename fallback
- topic filter rejects noise phrases from the legacy noise list
"""

from pathlib import Path

from wikify.ingest.metadata import (
    _parse_author_line,
    extract_authors_from_markdown,
    extract_document_doi,
    extract_doi,
    extract_publication_fields,
    extract_venue,
    extract_year_from_pdf_meta,
)
from wikify.ingest.pipeline import sections_from_chunks as _sections_from_chunks
from wikify.ingest.topics import _is_valid_keyword
from wikify.models import Chunk


def test_sections_from_chunks_groups_by_section_path():
    chunks = [
        Chunk(id="c0", doc_id="d", ord=0, text="a", char_span=(0, 1), section_path=["Intro"]),
        Chunk(id="c1", doc_id="d", ord=1, text="b", char_span=(0, 1), section_path=["Intro"]),
        Chunk(
            id="c2", doc_id="d", ord=2, text="c", char_span=(0, 1), section_path=["Results", "R1"]
        ),
        Chunk(
            id="c3",
            doc_id="d",
            ord=3,
            text="cap",
            char_span=(0, 1),
            section_path=["__image__", "d/fig_000"],
        ),
    ]
    sections = _sections_from_chunks(chunks)
    assert len(sections) == 2
    assert sections[0].path == ["Intro"]
    assert sections[0].chunk_ids == ["c0", "c1"]
    assert sections[1].path == ["Results", "R1"]
    assert sections[1].chunk_ids == ["c2"]


def test_sections_fallback_for_unheaded_document():
    chunks = [
        Chunk(id="c0", doc_id="d", ord=0, text="a", char_span=(0, 1), section_path=[]),
        Chunk(id="c1", doc_id="d", ord=1, text="b", char_span=(0, 1), section_path=[]),
    ]
    sections = _sections_from_chunks(chunks)
    assert len(sections) == 1
    assert sections[0].path == ["body"]
    assert sections[0].chunk_ids == ["c0", "c1"]


def test_year_returns_none_when_no_date_present():
    assert extract_year_from_pdf_meta({}) is None
    assert extract_year_from_pdf_meta({"creationDate": "garbage"}) is None
    # Real PDF date strings begin with "D:YYYYMMDD..."
    assert extract_year_from_pdf_meta({"creationDate": "D:20180507120000Z"}) == 2018


def test_author_line_strips_affiliation_superscripts_and_ampersand():
    line = "H. Kim 1,2, M. R. Mahmoodi 1, H. Nili 1 & D. B. Strukov 1"
    names = _parse_author_line(line)
    assert names == ["H. Kim", "M. R. Mahmoodi", "H. Nili", "D. B. Strukov"]


def test_extract_authors_from_markdown_beats_single_meta_author(tmp_path: Path):
    md = (
        "## ARTICLE\n\n"
        "## 4K-memristor analog-grade passive crossbar circuit\n\n"
        "H. Kim 1,2, M. R. Mahmoodi 1, H. Nili 1 & D. B. Strukov 1\n\n"
        "## Abstract\n\nContent here.\n"
    )
    names = extract_authors_from_markdown(md)
    assert len(names) == 4
    assert names[0] == "H. Kim"


def test_extract_venue_from_sciencedirect_homepage_heading():
    md = (
        "## Chemical Engineering Journal journal homepage: www.elsevier.com/locate/cej\n\n"
        "# Paper Title\n\n"
        "Abstract text."
    )
    assert extract_venue(md) == "Chemical Engineering Journal"


def test_extract_venue_ignores_generic_sciencedirect_homepage_line():
    md = (
        "Contents lists available at ScienceDirect journal homepage: "
        "www.elsevier.com/locate/jalcom\n\n"
        "# Paper Title\n\n"
        "Abstract text."
    )
    assert extract_venue(md) is None


def test_extract_venue_from_italic_volume_line():
    md = "_J. Appl. Phys._ 117, 044901 (2015)\n\n# Paper Title\n\nAbstract text."
    assert extract_venue(md) == "J. Appl. Phys."


def test_extract_publication_fields_from_italic_volume_line():
    md = "_J. Appl. Phys._ 117, 044901 (2015)\n\n# Paper Title\n\nAbstract text."
    assert extract_publication_fields(md) == {
        "venue": "J. Appl. Phys.",
        "volume": "117",
        "pages": "044901",
    }


def test_extract_publication_fields_from_acs_cite_this_line():
    md = (
        "**Cite This:** _ACS Materials Lett._ 2023, 5, 3080-3092 "
        "**Read Online**"
    )
    assert extract_publication_fields(md) == {
        "venue": "ACS Materials Lett.",
        "volume": "5",
        "pages": "3080-3092",
    }


def test_extract_publication_fields_from_published_by_line():
    md = (
        "Copyright 2023 The Authors. Advanced Functional Materials published by "
        "Wiley-VCH GmbH.\n\n# Paper"
    )
    assert extract_publication_fields(md) == {
        "venue": "Advanced Functional Materials",
    }


def test_extract_venue_from_trailing_heading_volume_line():
    md = (
        "# Memristor Paper\n\n"
        "Abstract text.\n\n"
        "## References\n\n"
        "1. Some reference. Nature 111, 1 (2000).\n\n"
        "## Nature 453, 80-83 (2008)\n"
    )
    assert extract_venue(md) == "Nature"


def test_extract_publication_fields_from_trailing_heading_volume_line():
    md = "# Paper\n\nBody.\n\n## Nature 453, 80-83 (2008)\n"
    assert extract_publication_fields(md) == {
        "venue": "Nature",
        "volume": "453",
        "pages": "80-83",
    }


def test_doi_extraction_strips_markdown_bracket_artifacts():
    md = "Available at http://dx.doi.org/10.1063/1.4905792]"
    assert extract_doi(md) == "10.1063/1.4905792"


def test_doi_extraction_strips_url_query_artifacts():
    md = "https://pubs.acs.org/action/showCitFormats?doi=10.1021/acsmaterialslett.3c00600&ref=pdf"
    assert extract_doi(md) == "10.1021/acsmaterialslett.3c00600"


def test_document_doi_ignores_references_section():
    md = "# Paper\n\nBody.\n\n## References\n\n1. Ref doi:10.1063/5.0093964"
    assert extract_document_doi(md) is None


def test_topic_filter_rejects_noise_phrases():
    # Historical noise that slipped through before the sweep.
    assert not _is_valid_keyword("above all")
    assert not _is_valid_keyword("compared with the set process")
    assert not _is_valid_keyword("ibility with cmos technology")
    assert not _is_valid_keyword("technology.6")
    # Real topics still pass.
    assert _is_valid_keyword("atomic layer deposition")
    assert _is_valid_keyword("memristor")
    assert _is_valid_keyword("oxygen vacancy")
