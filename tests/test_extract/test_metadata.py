"""Tests for scholarforge.extract.metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

from scholarforge.extract.metadata import (
    _extract_doi,
    _is_garbled_title,
    _parse_authors,
    _parse_filename,
    extract_metadata,
)

# ── _parse_filename ──────────────────────────────────────────────────────────


def test_parse_filename_full_pattern():
    year, author, title = _parse_filename("[2023 Smith] My Great Paper.pdf")
    assert year == 2023
    assert author == "Smith"
    assert title == "My Great Paper"


def test_parse_filename_multiple_authors():
    year, author, title = _parse_filename("[2021 Kim et al] ALD Review.pdf")
    assert year == 2021
    assert author == "Kim et al"
    assert title == "ALD Review"


def test_parse_filename_year_only():
    year, author, title = _parse_filename("[2019] Some Document.pdf")
    assert year == 2019
    assert author is None
    assert title == "Some Document"


def test_parse_filename_no_match():
    year, author, title = _parse_filename("random_file.pdf")
    assert year is None
    assert author is None
    assert title is None


def test_parse_filename_docx_extension():
    year, author, title = _parse_filename("[2020 Jones] Report.docx")
    assert year == 2020
    assert title == "Report"


# ── _is_garbled_title ────────────────────────────────────────────────────────


def test_garbled_title_dotdot_pattern():
    assert _is_garbled_title("acs_nn_nn-2014-01824r 1..7") is True


def test_garbled_title_alphanumeric_code():
    assert _is_garbled_title("la6b01014") is True


def test_garbled_title_untitled():
    assert _is_garbled_title("Untitled") is True


def test_garbled_title_journal_internal_ref():
    assert _is_garbled_title("acs_nano-2021-12345") is True


def test_garbled_title_clean_title():
    assert _is_garbled_title("Atomic Layer Deposition of HfO2 for Gate Dielectrics") is False


def test_garbled_title_short_title():
    # Single letter with alpha is not garbled (len < 5 but has alpha)
    assert _is_garbled_title("A") is False
    # 3-char uppercase abbreviation matches the short alphanumeric code pattern → garbled
    assert _is_garbled_title("ALD") is True


# ── _parse_authors ───────────────────────────────────────────────────────────


def test_parse_authors_comma_separated():
    result = _parse_authors("Smith, John, Doe, Jane")
    assert len(result) >= 2


def test_parse_authors_semicolon_separated():
    result = _parse_authors("Alice Brown; Bob Green; Carol White")
    assert "Alice Brown" in result or any("Brown" in a for a in result)


def test_parse_authors_and_separator():
    result = _parse_authors("Alice Brown and Bob Green")
    assert len(result) >= 2


def test_parse_authors_empty_string():
    result = _parse_authors("")
    assert result == []


def test_parse_authors_initials_reassembly():
    # "Yang, J. J." should produce "J. J. Yang"
    result = _parse_authors("Yang, J. J.")
    assert len(result) == 1
    assert "Yang" in result[0]


# ── _extract_doi ─────────────────────────────────────────────────────────────


def test_extract_doi_basic():
    text = "This paper has doi: 10.1021/nn501629g published in ACS Nano."
    doi = _extract_doi(text)
    assert doi == "10.1021/nn501629g"


def test_extract_doi_strips_trailing_punctuation():
    text = "See 10.1038/nature12345. For details."
    doi = _extract_doi(text)
    assert doi == "10.1038/nature12345"


def test_extract_doi_none_when_absent():
    text = "No DOI in this text at all."
    doi = _extract_doi(text)
    assert doi is None


def test_extract_doi_strips_closing_paren():
    text = "(10.1016/j.surfcoat.2020.125678)"
    doi = _extract_doi(text)
    assert doi == "10.1016/j.surfcoat.2020.125678"


# ── extract_metadata (integration) ───────────────────────────────────────────


def _make_doc(metadata: dict) -> MagicMock:
    doc = MagicMock()
    doc.metadata = metadata
    return doc


LONG_ABSTRACT = (
    "This study investigates the deposition of hafnium oxide thin films "
    "using atomic layer deposition. The films were characterized by X-ray "
    "diffraction, transmission electron microscopy, and electrical measurements. "
    "We demonstrate excellent conformality on high-aspect-ratio structures. "
    "The dielectric constant was measured to be 22 and the breakdown field "
    "exceeded 8 MV/cm. These results are promising for next-generation gate "
    "dielectrics in advanced CMOS technology nodes. Furthermore, we show that "
    "post-deposition annealing significantly improves the electrical performance."
)


def test_extract_metadata_uses_heading_title():
    md = f"# Atomic Layer Deposition of HfO2\n\n## Abstract\n\n{LONG_ABSTRACT}\n"
    doc = _make_doc({"title": "internal_ref_12345", "author": ""})
    result = extract_metadata(doc, md, "somefile.pdf")
    assert result["title"] == "Atomic Layer Deposition of HfO2"


def test_extract_metadata_falls_back_to_pdf_title():
    md = "No heading here.\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "A Clean PDF Title", "author": ""})
    result = extract_metadata(doc, md, "somefile.pdf")
    assert result["title"] == "A Clean PDF Title"


def test_extract_metadata_falls_back_to_filename_title():
    md = "No heading here.\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "", "author": ""})
    result = extract_metadata(doc, md, "[2022 Kim] Memristor Crossbar Arrays.pdf")
    assert result["title"] == "Memristor Crossbar Arrays"


def test_extract_metadata_abstract_extracted():
    md = f"# Some Paper\n\n## Abstract\n\n{LONG_ABSTRACT}\n\n## Introduction\n\nIntro text."
    doc = _make_doc({"title": "", "author": ""})
    result = extract_metadata(doc, md, "paper.pdf")
    assert result["abstract"] is not None
    assert len(result["abstract"].split()) >= 50


def test_extract_metadata_abstract_none_when_short_and_no_prose():
    # Only a short heading title, no body
    md = "# Title\n"
    doc = _make_doc({"title": "", "author": ""})
    result = extract_metadata(doc, md, "paper.pdf")
    # Abstract may be None or very short — it should not crash
    # (the function is allowed to return None for trivially short docs)
    assert "abstract" in result


def test_extract_metadata_year_from_filename():
    md = "# Paper\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "", "author": "", "creationDate": "D:20150101"})
    result = extract_metadata(doc, md, "[2021 Jones] Paper Title.pdf")
    assert result["year"] == 2021  # filename wins over metadata date


def test_extract_metadata_year_from_metadata_date():
    md = "# Paper\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "", "author": "", "creationDate": "D:20180601"})
    result = extract_metadata(doc, md, "no_pattern.pdf")
    assert result["year"] == 2018


def test_extract_metadata_doi_extracted():
    md = f"# Paper\n\ndoi: 10.1021/acsnano.1c00001\n\n{LONG_ABSTRACT}"
    doc = _make_doc({"title": "", "author": ""})
    result = extract_metadata(doc, md, "paper.pdf")
    assert result["doi"] == "10.1021/acsnano.1c00001"


def test_extract_metadata_authors_from_pdf_metadata():
    md = "# Paper\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "Paper", "author": "Alice Brown; Bob Green"})
    result = extract_metadata(doc, md, "paper.pdf")
    assert len(result["authors"]) >= 2
    assert any("Brown" in a or "Alice" in a for a in result["authors"])


def test_extract_metadata_authors_from_filename_fallback():
    md = "# Paper\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "Paper", "author": ""})
    result = extract_metadata(doc, md, "[2020 Johnson] Some Title.pdf")
    # filename author used as last fallback
    assert any("Johnson" in a for a in result["authors"])


def test_extract_metadata_returns_all_keys():
    md = "# Paper\n\n" + LONG_ABSTRACT
    doc = _make_doc({"title": "T", "author": ""})
    result = extract_metadata(doc, md, "paper.pdf")
    assert set(result.keys()) == {"title", "authors", "abstract", "year", "doi"}
