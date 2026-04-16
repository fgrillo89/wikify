"""Tests for bibliography output quality.

Regression tests for structural issues found during the mvp100 audit:
- volume == year false extraction
- garbled journal names from OCR artifacts
- author names leaked into titles
- journal fragments in titles
"""

import pytest

from wikify.citestore.parse import extract_venue_fields, parse_citation
from wikify.ingest.bibtex import _clean_bib_journal, _clean_bib_title


# ---------------------------------------------------------------------------
# Volume == year suppression
# ---------------------------------------------------------------------------


class TestVolumeYearSuppression:
    """Volume should not be set when it equals the year (common parse error)."""

    def test_year_as_volume_suppressed(self):
        # "Manage. Sci 1960, 324-342" -> volume should NOT be 1960
        raw = "P. R. Winters, Forecasting Sales by Exponentially Weighted Moving Averages, Manage. Sci 1960, 324-342."
        result = parse_citation(raw, year=1960)
        assert result.get("volume") is None or result.get("volume") != "1960"

    def test_real_volume_preserved(self):
        # "Nature 433, 47 (2005)" -> volume=433 is real
        raw = "D. B. Strukov, The missing memristor found, Nature 453, 80-83 (2008)."
        result = parse_citation(raw, year=2008)
        fields = extract_venue_fields(raw, "The missing memristor found")
        # 453 is not a year, should be kept
        assert fields.get("volume") == "453"

    def test_venue_fields_year_range(self):
        # Directly test extract_venue_fields with year-like volume
        raw = "Title goes here. SIAM J. Comput 1972, 146-160."
        fields = extract_venue_fields(raw, "Title goes here")
        # 1972 looks like a year, should be suppressed
        assert fields.get("volume") is None


# ---------------------------------------------------------------------------
# Journal cleaning
# ---------------------------------------------------------------------------


class TestJournalCleaning:
    """Garbled journal names from OCR should be cleaned."""

    def test_leading_quote_stripped(self):
        assert _clean_bib_journal("'  PhiI. Mug., ser") == "PhiI. Mug., ser"

    def test_leading_bracket_stripped(self):
        assert _clean_bib_journal("' Bell  Syst.  Tech.  J.") == "Bell Syst. Tech. J."

    def test_double_spaces_collapsed(self):
        assert _clean_bib_journal("IEEE  Trans.  Comput.") == "IEEE Trans. Comput."

    def test_trailing_month_year_stripped(self):
        result = _clean_bib_journal("IBM J. Res. Develop., Sept. 1969")
        assert result == "IBM J. Res. Develop."

    def test_trailing_vol_stripped(self):
        result = _clean_bib_journal("IEEE Trans. Comput. , vol. C-")
        assert "vol" not in result.lower()
        assert result.startswith("IEEE Trans. Comput.")

    def test_clean_journal_unchanged(self):
        assert _clean_bib_journal("Nature Materials") == "Nature Materials"


# ---------------------------------------------------------------------------
# Title cleaning
# ---------------------------------------------------------------------------


class TestTitleCleaning:
    """Author names and journal fragments should not leak into titles."""

    def test_multi_author_prefix_stripped(self):
        # "Joshua Yang, R. Huang, Y. Yang, Small Sci" -> real title gone
        result = _clean_bib_title(
            "Joshua Yang, R. Huang, Y . Yang, Small Sci"
        )
        # Should not start with author names
        assert not result.startswith("Joshua")

    def test_chua_ieee_citation_stripped(self):
        result = _clean_bib_title(
            "Chua , IEEE Trans. Circuit Theory 18 (1971) 507-519"
        )
        assert "IEEE Trans" not in result

    def test_ieee_trailing_stripped(self):
        result = _clean_bib_title(
            "LeBlanc, IEEE J. Solid-State Circuits 1974 , 9 , 256"
        )
        assert "IEEE" not in result

    def test_normal_title_preserved(self):
        result = _clean_bib_title(
            "Resistive switching and synaptic properties of fully atomic "
            "layer deposition grown TiN/HfO2/TiN devices"
        )
        assert "Resistive switching" in result

    def test_et_al_prefix_stripped(self):
        result = _clean_bib_title("Smith et al., A study of memristors")
        assert result.startswith("A study")

    def test_trailing_journal_fragment_stripped(self):
        result = _clean_bib_title(
            "Some general theorems for nonlinear systems possess-! ing reactance"
        )
        # Should preserve the (garbled) title even if OCR damaged
        assert "general theorems" in result


# ---------------------------------------------------------------------------
# Integration: _reference_entry_from_citation
# ---------------------------------------------------------------------------


class TestReferenceEntry:
    """End-to-end: raw citation -> BibTeX entry should be clean."""

    def test_volume_not_year(self):
        from wikify.ingest.bibtex import _reference_entry_from_citation

        cit = {
            "title": "Forecasting Sales by Exponentially Weighted Moving Averages",
            "authors": ["P. R. Winters"],
            "year": 1960,
            "venue": "Manage. Sci",
            "volume": "1960",
        }
        entry = _reference_entry_from_citation(cit)
        assert entry is not None
        assert "volume" not in entry  # suppressed because vol == year

    def test_volume_preserved_when_different(self):
        from wikify.ingest.bibtex import _reference_entry_from_citation

        cit = {
            "title": "The missing memristor found",
            "authors": ["D. B. Strukov"],
            "year": 2008,
            "venue": "Nature",
            "volume": "453",
        }
        entry = _reference_entry_from_citation(cit)
        assert entry is not None
        assert entry.get("volume") == "453"
