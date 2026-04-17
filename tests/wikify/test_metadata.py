"""Regression tests for ingest metadata helpers.

Covers author parsing fixes:
- hyphenated Chinese given names (``Tian-Yu``, ``Jia-Lin``) are not split
  apart by the surname/given reassembler
- trailing single-letter affiliation markers (``Mi Hyang Park a``) are
  stripped without damaging proper initials (``J. Smith``)
- affiliation digits glued directly to a surname (``Wang1``) are stripped
"""

from __future__ import annotations

from wikify.ingest.bibtex import _clean_author_name
from wikify.ingest.metadata import (
    _parse_author_line,
    _strip_trailing_affiliation_letter,
    parse_authors,
)

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
