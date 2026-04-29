"""Regression tests for ingest metadata helpers.

Covers the fixes for:
- Bug 2 (junk title rejection): ``Word Document``, ``Untitled``, empty,
  ``Document1``, ``Microsoft Word - foo.docx`` and venue-matching strings
  must not be accepted as titles. ``clean_filename_title`` recovers a
  readable title from ``[YYYY Author] Foo_<hash>.ext`` filenames.
- Bug 6 (author parsing): hyphenated Chinese given names are not split
  apart; trailing single-letter affiliation markers are stripped without
  damaging proper initials.
"""

from __future__ import annotations

from pathlib import Path

from wikify.ingest.bibtex import _clean_author_name
from wikify.ingest.metadata import (
    _parse_author_line,
    _strip_trailing_affiliation_letter,
    choose_document_title,
    clean_filename_title,
    is_junk_title,
    parse_authors,
)

# ---------------------------------------------------------------------------
# Junk title rejection
# ---------------------------------------------------------------------------


class TestIsJunkTitle:
    def test_word_document_rejected(self):
        assert is_junk_title("Word Document") is True

    def test_word_document_case_insensitive(self):
        assert is_junk_title("word document") is True
        assert is_junk_title("WORD DOCUMENT") is True

    def test_untitled_rejected(self):
        assert is_junk_title("Untitled") is True
        assert is_junk_title("untitled.docx") is True

    def test_empty_rejected(self):
        assert is_junk_title("") is True
        assert is_junk_title("   ") is True

    def test_document1_rejected(self):
        assert is_junk_title("Document1") is True
        assert is_junk_title("Document 1") is True

    def test_microsoft_word_prefix_rejected(self):
        assert is_junk_title("Microsoft Word - manuscript.docx") is True
        assert is_junk_title("Microsoft Word - Paper final") is True

    def test_venue_as_title_rejected(self):
        # Journal name leaked into the title slot.
        venues = ("Journal of Alloys and Compounds",)
        assert is_junk_title(
            "Journal of Alloys and Compounds", venue_hints=venues,
        ) is True

    def test_venue_match_case_insensitive(self):
        venues = ("Nature Communications",)
        assert is_junk_title("nature communications", venue_hints=venues) is True

    def test_real_title_accepted(self):
        assert is_junk_title(
            "Atomic Layer Deposition of HfO2 for Memristive Synapses",
        ) is False

    def test_garbled_hash_rejected(self):
        # is_garbled_title catches "abc_def123" style tokens.
        assert is_junk_title("fn1_x2") is True

    # ------------- Section-header literals (observed in the wild) -----------

    def test_section_headers_rejected(self):
        # Each of these actually leaked into corpus_papers.bib once from a
        # paper where Marker tagged the section label as the first heading.
        for t in (
            "Abstract", "Introduction", "Conclusions", "References",
            "Methods", "Results", "Discussion", "Acknowledgements",
            "Supporting Information",
            "Conflict of Interest",
        ):
            assert is_junk_title(t) is True, t

    def test_section_header_case_insensitive(self):
        assert is_junk_title("ABSTRACT") is True
        assert is_junk_title("conclusions") is True

    def test_numbered_section_rejected(self):
        # `_NUMBERED_SECTION_RE` catches arabic, roman, lettered, and
        # mixed-punctuation numbering as long as the whole thing is <= 3 words.
        for t in (
            "1 Introduction",
            "2. Methods",
            "III. Results",
            "IV Discussion",
        ):
            assert is_junk_title(t) is True, t

    def test_long_numbered_heading_accepted(self):
        # A legitimate book-chapter title that happens to start with a
        # number must survive — more than 3 words is the guard.
        assert is_junk_title("1 Introduction to Atomic Layer Deposition") is False

    # ------------- Repository / institutional banners ----------------------

    def test_repository_banner_rejected(self):
        # Marker picks up the first-page banner as a heading on some
        # thesis / repository PDFs.
        for t in (
            "University of Central Florida",
            "Institute of Physics",
            "School of Chemistry",
            "Department of Materials Science",
            "College of Engineering",
        ):
            assert is_junk_title(t) is True, t

    # ------------- Markdown link fragment ----------------------------------

    def test_markdown_link_fragment_rejected(self):
        # Observed: Marker emitted the header's hyperlinked venue as the
        # first heading.
        assert is_junk_title(
            "[Materials Today: Proceedings xxx \\(xxxx\\) xxx](",
        ) is True
        assert is_junk_title("[www.acsanm.org](www.acsanm.org?ref=pdf)") is True

    def test_plain_title_with_brackets_accepted(self):
        # Brackets alone are fine; the junk pattern requires the `](` link
        # syntax.
        assert is_junk_title(
            "[2022 Ismail] Forming-free Pt Al2O3 TiN memristor",
        ) is False


# ---------------------------------------------------------------------------
# Filename title recovery
# ---------------------------------------------------------------------------


class TestCleanFilenameTitle:
    def test_full_bracket_prefix_and_hash(self):
        name = (
            "[2022 Ismail] Forming-free Pt Al2O3 HfO2 HfAlOx TiN memristor"
            " with controllable_ae0430fe3c3f.pdf"
        )
        out = clean_filename_title(name)
        assert "2022" not in out
        assert "Ismail" not in out
        assert "ae0430fe3c3f" not in out
        assert out.startswith("Forming free Pt Al2O3 HfO2")

    def test_docx_extension(self):
        name = "[1971 Chua] Memristor-The_missing_circuit_element_514791d621fa.docx"
        out = clean_filename_title(name)
        assert "514791d621fa" not in out
        assert "Memristor" in out
        assert "missing circuit element" in out

    def test_no_brackets(self):
        name = "plain_title_file_1234567890ab.pdf"
        out = clean_filename_title(name)
        assert "1234567890ab" not in out
        assert out == "plain title file"

    def test_empty(self):
        assert clean_filename_title("") == ""


# ---------------------------------------------------------------------------
# _clean_author_name behavioural guarantees
#
# The bibtex helper is imported here to keep the regression battery in one
# place alongside the parsers that feed it.
# ---------------------------------------------------------------------------


class TestCleanAuthorName:
    def test_hyphenated_chinese_given_name_unchanged(self):
        # Mixed-case name: _clean_author_name must not alter it.
        assert _clean_author_name("Tian-Yu") == "Tian-Yu"

    def test_all_caps_surname_title_cased(self):
        assert _clean_author_name("SMITH") == "Smith"

    def test_particle_preserved(self):
        # Particles are preserved lowercase in non-leading position.
        assert _clean_author_name("peter van der waals") == "Peter van der Waals"


# ---------------------------------------------------------------------------
# parse_authors: both orderings produce two authors
# ---------------------------------------------------------------------------


class TestParseAuthors:
    def test_surname_given_semicolons(self):
        # "Wang, Tian-Yu; Meng, Jia-Lin" should assemble into two authors
        # with the hyphenated given name reattached to the surname.
        out = parse_authors("Wang, Tian-Yu; Meng, Jia-Lin")
        assert len(out) == 2
        assert out[0] == "Tian-Yu Wang"
        assert out[1] == "Jia-Lin Meng"

    def test_given_surname_commas(self):
        # "Tian-Yu Wang, Jia-Lin Meng" is already in given-surname order.
        out = parse_authors("Tian-Yu Wang, Jia-Lin Meng")
        assert len(out) == 2
        assert out[0] == "Tian-Yu Wang"
        assert out[1] == "Jia-Lin Meng"

    def test_trailing_affiliation_letter_stripped(self):
        # "Mi Hyang Park a, Thanh Luan Phan a" — both authors should have
        # their trailing " a" affiliation markers removed.
        out = parse_authors("Mi Hyang Park a, Thanh Luan Phan a")
        assert out == ["Mi Hyang Park", "Thanh Luan Phan"]


# ---------------------------------------------------------------------------
# Low-level trailing-letter strip
# ---------------------------------------------------------------------------


class TestStripTrailingAffiliationLetter:
    def test_trailing_letter_stripped(self):
        assert _strip_trailing_affiliation_letter("Mi Hyang Park a") == "Mi Hyang Park"

    def test_double_marker_stripped(self):
        # "Van Tu Vu a a" collapses in the ``while`` loop.
        assert _strip_trailing_affiliation_letter("Van Tu Vu a a") == "Van Tu Vu"

    def test_initial_preserved(self):
        # Proper initials end in a period; the regex is anchored on
        # preceding lowercase, so initials survive.
        assert _strip_trailing_affiliation_letter("J. Smith") == "J. Smith"

    def test_short_name_untouched(self):
        # Nothing to strip.
        assert _strip_trailing_affiliation_letter("Lin Chen") == "Lin Chen"


# ---------------------------------------------------------------------------
# _parse_author_line: affiliation digit + letter artifact cleanup
# ---------------------------------------------------------------------------


class TestParseAuthorLineArtifacts:
    def test_digits_glued_to_surname_removed(self):
        line = (
            "Tian-Yu Wang1, Jia-Lin Meng2, Zhen-Yu He1, Lin Chen1, "
            "Hao Zhu1, Qing-Qing Sun1, Shi-Jin Ding1 and David Wei Zhang1"
        )
        out = _parse_author_line(line)
        # Every author must be parsed -- no trailing digits allowed.
        assert len(out) == 8
        assert "Tian-Yu Wang" in out
        assert "Jia-Lin Meng" in out
        assert "David Wei Zhang" in out
        for name in out:
            assert not name[-1].isdigit(), name

    def test_trailing_affiliation_letters_stripped(self):
        line = (
            "Thi Thanh Huong Vu a, Mi Hyang Park a, Thanh Luan Phan a, "
            "Hyun Jun Park a, Van Tu Vu a"
        )
        out = _parse_author_line(line)
        assert len(out) == 5
        for name in out:
            # No name should end with a single-letter affiliation marker.
            last_token = name.split()[-1]
            assert len(last_token) > 1 or last_token.endswith("."), name


# ---------------------------------------------------------------------------
# choose_document_title — filename underscores must round-trip as spaces
# ---------------------------------------------------------------------------


class TestChooseDocumentTitleUnderscores:
    """In the ``[YYYY Author] Title.ext`` filename convention,
    underscores stand for spaces. Without an explicit underscore->space
    map, ``clean_markdown`` (called inside ``choose_document_title``)
    treats `_word_` as italic markup and eats the underscores along
    with the inner text — so ``Memristor-The_missing_circuit_element``
    used to collapse to ``Memristor-Themissingcircuit_element``.
    """

    def test_filename_underscores_become_spaces_for_chua(self):
        chosen = choose_document_title(
            "",
            Path("[1971 Chua] Memristor-The_missing_circuit_element.docx"),
        )
        assert chosen == "Memristor-The missing circuit element"

    def test_filename_underscores_no_eaten_words(self):
        chosen = choose_document_title(
            "",
            Path(
                "[2020 Smith] Conductive_Filament_Dynamics_in_HfO2.docx",
            ),
        )
        # Every word from the filename must survive — no italic-collapse.
        for word in ("Conductive", "Filament", "Dynamics", "HfO2"):
            assert word in chosen, (word, chosen)

    def test_already_spaced_filename_unchanged(self):
        # Filenames that already use spaces must round-trip without
        # gaining or losing whitespace.
        chosen = choose_document_title(
            "",
            Path("[2020 Smith] Some Already Spaced Title.docx"),
        )
        assert chosen == "Some Already Spaced Title"
