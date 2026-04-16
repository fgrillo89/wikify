"""Regression tests for the Marker PDF parser post-processing.

Covers the fixes from the PR #14 follow-up review:
- ``<sup>N</sup>`` superscript citations become ``[N]``
- ``word20-22`` concatenated refs get split so bracketize can match them
- Marker image links are stripped from persisted markdown
- Caption-derived figure labels, not block-id labels
- HTML ``<sup>`` affiliation markup does not leak into author metadata
"""

from __future__ import annotations

from wikify.ingest.parsers._citations import (
    bracketize_bare_refs,
    bracketize_sup_refs,
    count_ref_list_items_from_md,
    split_adjacent_refs,
)
from wikify.ingest.parsers.marker_pdf import (
    _label_from_caption,
    _sanitize_author,
    _strip_image_links,
)

# ---------------------------------------------------------------------------
# Sup-tag citation bracketing
# ---------------------------------------------------------------------------


class TestBracketizeSupRefs:
    def test_single_digit_sup_bracketed(self):
        assert bracketize_sup_refs("devices<sup>1</sup>.") == "devices[1]."

    def test_range_sup_bracketed(self):
        assert bracketize_sup_refs("devices<sup>2-19</sup>.") == "devices[2-19]."

    def test_comma_list_sup_bracketed(self):
        assert bracketize_sup_refs("work<sup>1,2,3</sup>.") == "work[1,2,3]."

    def test_letter_sup_unchanged(self):
        # <sup>a</sup>-style affiliations must NOT be turned into brackets.
        assert bracketize_sup_refs("Patnaik<sup>b</sup>") == "Patnaik<sup>b</sup>"

    def test_chemistry_sup_unchanged(self):
        # Oxidation-state superscripts contain non-digit chars and stay put.
        assert bracketize_sup_refs("Cu<sup>2+</sup>") == "Cu<sup>2+</sup>"

    def test_empty_body_unchanged(self):
        assert bracketize_sup_refs("") == ""
        assert bracketize_sup_refs("no sup here") == "no sup here"


# ---------------------------------------------------------------------------
# Adjacent-ref splitting
# ---------------------------------------------------------------------------


class TestSplitAdjacentRefs:
    def test_range_split(self):
        assert split_adjacent_refs("switches20-22.") == "switches 20-22."

    def test_comma_list_split(self):
        assert split_adjacent_refs("devices2,3,4.") == "devices 2,3,4."

    def test_single_number_not_split(self):
        # "version5" could be a product name; require a list/range.
        assert split_adjacent_refs("version5.") == "version5."

    def test_short_word_not_split(self):
        # "CO2" / "H2O" style must not be split.
        assert split_adjacent_refs("CO2-3.") == "CO2-3."

    def test_bracketize_bare_refs_integration(self):
        # Full Marker flow: split, then bracketize with a ref count.
        md = "switches20-22."
        split = split_adjacent_refs(md)
        result = bracketize_bare_refs(split, ref_count=30)
        assert "[20-22]" in result


# ---------------------------------------------------------------------------
# Bibliography counting from markdown
# ---------------------------------------------------------------------------


class TestCountRefsFromMd:
    def test_numbered_list_counted(self):
        md = (
            "# Paper\n\n"
            "Body text.\n\n"
            "# References\n\n"
            "1. First ref.\n"
            "2. Second ref.\n"
            "3. Third ref.\n"
        )
        assert count_ref_list_items_from_md(md) == 3

    def test_bracket_numbered_counted(self):
        md = (
            "## References\n\n"
            "[1] First ref.\n"
            "[2] Second ref.\n"
        )
        assert count_ref_list_items_from_md(md) == 2

    def test_no_refs_section(self):
        assert count_ref_list_items_from_md("# Body\n\nNo refs.") == 0

    def test_uses_last_references_heading(self):
        # Multiple "References" headings: use the last.
        md = (
            "# References (early mention)\n\n"
            "Some prose.\n\n"
            "# References\n\n"
            "1. Real ref.\n"
            "2. Another.\n"
        )
        assert count_ref_list_items_from_md(md) == 2


# ---------------------------------------------------------------------------
# Image-link stripping
# ---------------------------------------------------------------------------


class TestStripImageLinks:
    def test_basic_image_link_removed(self):
        md = "Body before.\n\n![](img.png)\n\nBody after."
        result = _strip_image_links(md)
        assert "img.png" not in result
        assert "Body before" in result and "Body after" in result

    def test_marker_block_link_removed(self):
        md = "See figure: ![caption](_page_0_Figure_18.jpeg)"
        result = _strip_image_links(md)
        assert "_page_0_Figure_18" not in result

    def test_empty_input(self):
        assert _strip_image_links("") == ""


# ---------------------------------------------------------------------------
# Caption label parsing
# ---------------------------------------------------------------------------


class TestLabelFromCaption:
    def test_figure_number_extracted(self):
        assert _label_from_caption("Figure 1. Schematic of device.") == "Figure 1"

    def test_figure_abbreviated(self):
        assert _label_from_caption("Fig. 3 shows results.") == "Figure 3"

    def test_table_number_extracted(self):
        assert _label_from_caption("Table 2. Summary.") == "Table 2"

    def test_sub_letter_kept(self):
        assert _label_from_caption("Figure 4a. Detail view.") == "Figure 4a"

    def test_no_prefix_returns_none(self):
        assert _label_from_caption("Schematic showing...") is None

    def test_empty_returns_none(self):
        assert _label_from_caption("") is None


# ---------------------------------------------------------------------------
# Author sanitization
# ---------------------------------------------------------------------------


class TestSanitizeAuthor:
    def test_numeric_sup_already_bracketed(self):
        # Numeric sups are converted to [1] by bracketize_sup_refs upstream;
        # _parse_author_line drops bracket content. _sanitize_author is the
        # last-mile guard for anything the line parser missed.
        assert _sanitize_author("Dmitri B. Strukov") == "Dmitri B. Strukov"

    def test_letter_sup_stripped(self):
        assert _sanitize_author("Asutosh Patnaik<sup>b</sup>") == "Asutosh Patnaik"

    def test_letter_sup_with_space_stripped(self):
        assert _sanitize_author("Asutosh Patnaik <sup>b</sup>") == "Asutosh Patnaik"

    def test_clean_name_unchanged(self):
        assert _sanitize_author("Jane Q. Smith") == "Jane Q. Smith"

    def test_trailing_punctuation_stripped(self):
        assert _sanitize_author("Smith,") == "Smith"
