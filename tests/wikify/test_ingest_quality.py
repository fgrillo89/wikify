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
