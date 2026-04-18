"""Regression tests for the Marker PDF parser post-processing.

Covers the fixes from the PR #14 follow-up review:
- ``<sup>N</sup>`` superscript citations become ``[N]``
- ``word20-22`` concatenated refs are bracketed even mid-sentence
- Bullet-style bibliography entries (``- 1. Chua...``) are counted
- Marker image links are stripped from persisted markdown
- Caption-derived figure labels, not block-id labels
- HTML ``<sup>`` affiliation markup does not leak into author metadata
"""

from __future__ import annotations

from wikify.ingest.metadata import _strip_inline_markup as _sanitize_author
from wikify.ingest.parsers._citations import (
    bracketize_concat_refs,
    bracketize_sup_refs,
    count_ref_list_items_from_md,
)
from wikify.ingest.parsers.marker_pdf import (
    _label_from_caption,
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
# Concat-ref bracketing
# ---------------------------------------------------------------------------


class TestBracketizeConcatRefs:
    def test_range_bracketed(self):
        assert (
            bracketize_concat_refs("switches20-22.", ref_count=30)
            == "switches [20-22]."
        )

    def test_comma_list_bracketed(self):
        assert (
            bracketize_concat_refs("devices2,3,4.", ref_count=30)
            == "devices [2,3,4]."
        )

    def test_mid_sentence_range_bracketed(self):
        # Regression: reviewer flagged "devices2-19 involve..." staying bare
        # because the bare-refs pass skips lowercase continuation words.
        md = "devices2-19 involve motion, switches20-22."
        out = bracketize_concat_refs(md, ref_count=30)
        assert "devices [2-19]" in out
        assert "switches [20-22]" in out

    def test_single_number_not_bracketed(self):
        # "version5" could be a product name; require a list/range.
        assert bracketize_concat_refs("version5.", ref_count=30) == "version5."

    def test_short_word_not_bracketed(self):
        # "CO2-3" must not be treated as a citation.
        assert bracketize_concat_refs("CO2-3.", ref_count=30) == "CO2-3."

    def test_out_of_range_not_bracketed(self):
        # 100-200 with only 30 refs: stays untouched.
        assert (
            bracketize_concat_refs("switches100-200.", ref_count=30)
            == "switches100-200."
        )

    def test_no_refs_section_skips(self):
        assert bracketize_concat_refs("switches20-22.", ref_count=0) == "switches20-22."


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

    def test_bullet_numbered_counted(self):
        # Marker emits bibliography as "- 1. Author..." bullet entries.
        md = (
            "## References\n\n"
            "- 1. Chua, L. Memristor.\n"
            "- 2. Strukov, D. Missing memristor.\n"
            "- 3. Williams, R.\n"
        )
        assert count_ref_list_items_from_md(md) == 3

    def test_no_refs_section(self):
        assert count_ref_list_items_from_md("# Body\n\nNo refs.") == 0

    def test_fallback_cluster_without_heading(self):
        # No "References" heading, but a dense trailing numbered cluster.
        md = (
            "# Intro\n\nBody text.\n\n"
            "1. Chua, L. Memristor.\n"
            "2. Strukov, D. Missing memristor.\n"
            "3. Williams, R. HP memristor.\n"
            "4. Yang, J. Synaptic.\n"
            "5. Kim, S. ALD memristor.\n"
            "6. Lee, C. TiN/HfO2.\n"
        )
        assert count_ref_list_items_from_md(md) == 6

    def test_fallback_short_cluster_rejected(self):
        # Three trailing numbered items is below the cluster threshold
        # and must not be mistaken for a bibliography.
        md = (
            "# Methods\n\nSteps:\n\n"
            "1. Do thing.\n"
            "2. Do other thing.\n"
            "3. Done.\n"
        )
        assert count_ref_list_items_from_md(md) == 0

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
