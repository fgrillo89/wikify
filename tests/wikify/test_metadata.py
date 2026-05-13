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

from wikify.ingest.bibtex import _clean_author_name, _clean_title
from wikify.ingest.metadata import (
    _looks_like_journal_name,
    _looks_like_reference_list,
    _parse_author_line,
    _strip_inline_markup,
    _strip_trailing_affiliation_letter,
    choose_document_title,
    clean_filename_title,
    clean_markdown,
    extract_authors_from_markdown,
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


# ---------------------------------------------------------------------------
# Author glyph stripping + all-caps title-casing (root-cause fixes C2, C3)
# ---------------------------------------------------------------------------


class TestStripInlineMarkupGlyphs:
    """``_strip_inline_markup`` is the universal post-sanitation hook for
    author strings (called from ``assemble_pdf_metadata``). It must
    handle publisher-specific footnote glyphs and IEEE-style ALL-CAPS
    bylines, not just the original ``<sup>``/``<sub>`` markup."""

    def test_strips_private_use_area_glyph(self):
        # Wiley custom font footnote markers land in the PUA range.
        assert _strip_inline_markup("Jun Li ") == "Jun Li"

    def test_strips_invalid_codepoints(self):
        # Some publishers misuse U+0378 / U+0379 (officially undefined).
        assert _strip_inline_markup("Kitae Park ͸") == "Kitae Park"
        assert _strip_inline_markup("Liudi Jiang ͹") == "Liudi Jiang"

    def test_strips_greek_archaic_koppa(self):
        # Greek archaic koppa (U+0377) used as a footnote anchor.
        assert _strip_inline_markup("Daniel Newbrook ͷ") == "Daniel Newbrook"

    def test_strips_asterisk_operator(self):
        # U+2217 ASTERISK OPERATOR (not the ASCII *).
        assert _strip_inline_markup("Bernabé Linares-Barranco ∗") == (
            "Bernabé Linares-Barranco"
        )

    def test_strips_lone_spacing_modifier_tilde(self):
        # When the diacritic got separated from its letter ("Su ñ e"
        # became "Su ˜ e"), at least drop the floating tilde.
        out = _strip_inline_markup("J. Su ˜ ne")
        assert "˜" not in out

    def test_titlecases_all_caps_name(self):
        assert _strip_inline_markup("DEBASHIS PANDA") == "Debashis Panda"

    def test_titlecases_hyphenated_all_caps(self):
        # Hyphen-separated tokens get capitalised per-token.
        assert _strip_inline_markup("PEI-YU JUNG") == "Pei-Yu Jung"

    def test_preserves_mixed_case_name(self):
        # Mixed case must NOT be re-cased: van der Waals, McMaster, etc.
        assert _strip_inline_markup("Hai (Helen) Li") == "Hai (Helen) Li"
        assert _strip_inline_markup("Bernabé Linares-Barranco") == (
            "Bernabé Linares-Barranco"
        )

    def test_existing_sup_sub_markup_still_stripped(self):
        # Original responsibility: drop affiliation HTML markup.
        assert _strip_inline_markup("Sungjun Kim<sup>1,2</sup>") == "Sungjun Kim"
        assert _strip_inline_markup("Sungjun Kim<sub>1,2</sub>") == "Sungjun Kim"

    def test_preserves_hawaiian_okina(self):
        # ʻokina (U+02BB) is a real letter in Hawaiian / Samoan names.
        # The narrowed glyph range (U+02D0-U+02FF) excludes the
        # transliteration-apostrophe band so it survives.
        assert _strip_inline_markup("Keʻalohi") == "Keʻalohi"

    def test_preserves_modifier_letter_apostrophe(self):
        # ʼ (U+02BC) is the canonical apostrophe for transliterated
        # names. Must not be stripped.
        assert _strip_inline_markup("Suʼne") == "Suʼne"

    def test_preserves_modifier_prime(self):
        # ʹ (U+02B9) is the modifier prime used in Cyrillic
        # transliteration (e.g., ``Solovʹev``).
        assert _strip_inline_markup("Solovʹev") == "Solovʹev"


# ---------------------------------------------------------------------------
# Slug-shaped title detection (root-cause fix C5)
# ---------------------------------------------------------------------------


class TestCleanMarkdownSlugRecovery:
    """``clean_markdown`` is the common pre-filter for every title
    candidate fed into ``choose_document_title``. When the candidate is
    a pure URL/slug shape (5+ hyphens, no spaces, all lowercase) it
    converts back to readable title case so the publisher-derived
    filename doesn't survive as the corpus title."""

    def test_unslug_hyphen_joined_filename(self):
        out = clean_markdown("artificial-synapse-based-on-a-bilayer-memristor")
        assert out == "Artificial Synapse Based On A Bilayer Memristor"

    def test_plain_title_unchanged(self):
        assert clean_markdown("Normal Paper Title") == "Normal Paper Title"

    def test_short_hyphen_title_unchanged(self):
        # Only 2 hyphens: a normal compound title, not a slug.
        out = clean_markdown("In-Memory Computing with Memristor Arrays")
        assert out == "In-Memory Computing with Memristor Arrays"

    def test_internal_spaces_disable_slug_recovery(self):
        # A title with both hyphens and spaces is already real text;
        # don't touch it.
        out = clean_markdown("low-cost-rapid-prototyping-system for memristors")
        assert out == "low-cost-rapid-prototyping-system for memristors"


# ---------------------------------------------------------------------------
# CrossRef title sanitisation (root-cause fixes B1, B2)
# ---------------------------------------------------------------------------


class TestCleanTitleStripsCrossRefMarkup:
    """``bibtex._clean_title`` is called on every CrossRef-derived
    title before it overwrites local metadata. It must strip JATS
    inline markup, unescape HTML entities, and collapse whitespace so
    no raw <sub>/<sup>/<i> tags survive in ``documents.title`` or in
    ``metadata_json``."""

    def test_strips_sub_tag(self):
        out = _clean_title("Memristor based on HfO<sub>2</sub>")
        assert out == "Memristor based on HfO2"

    def test_strips_italic_tag(self):
        out = _clean_title("Switching of AlO<i>x</i> films")
        assert out == "Switching of AlOx films"

    def test_unescapes_amp(self):
        assert _clean_title("R &amp; D advances") == "R & D advances"

    def test_collapses_embedded_newlines(self):
        # Some CrossRef titles ship JATS pretty-printed with literal
        # \n + indent inside the title value.
        dirty = (
            "Doping Engineering for Optimized TaO\n"
            "                    <sub>x</sub>\n"
            "                    Memristor"
        )
        assert _clean_title(dirty) == "Doping Engineering for Optimized TaOx Memristor"

    def test_preserves_inline_single_space(self):
        # Inline single-space adjacency to a tag must NOT be eaten:
        # ``"Foo <sub>x</sub> bar"`` should yield ``"Foo x bar"`` (one
        # space on each side), not ``"Foox bar"`` or ``"Foox bar"``.
        # Only the JATS pretty-print case (newline + indent) gets
        # collapsed.
        assert _clean_title("Foo <sub>x</sub> bar") == "Foo x bar"
        assert _clean_title("paper on AlO <sub>x</sub> films") == (
            "paper on AlO x films"
        )


# ---------------------------------------------------------------------------
# XMP author flattening (root-cause fix C1)
# ---------------------------------------------------------------------------


class TestParseAuthorsFlattensStuffedXmp:
    """When a publisher's XMP ``dc:creator`` field stuffs the entire
    byline into a single rdf:li (instead of one entry per author),
    ``assemble_pdf_metadata`` passes that one string through
    ``parse_authors`` to flatten. This regression verifies the splitter
    returns the expected name list for the failing real-world case."""

    def test_flattens_comma_separated_byline(self):
        raw = (
            "Hadiyawarman, Faisal Budiman, Detiza Goldianto Octensi Hernowo, "
            "Reetu Raj Pandey, Hirofumi Tanaka"
        )
        names = parse_authors(raw)
        assert len(names) == 5
        assert "Hadiyawarman" in names
        assert "Faisal Budiman" in names
        assert "Hirofumi Tanaka" in names

    def test_flattens_semicolon_separated_byline(self):
        # AIP landing pages use semicolons inside the dc:creator field.
        raw = "Yu. Matveyev; K. Egorov; A. Markeev; A. Zenkevich"
        names = parse_authors(raw)
        assert len(names) >= 3

    def test_single_name_pass_through(self):
        # A clean single-author XMP value must not get mangled.
        assert parse_authors("Sungjun Kim") == ["Sungjun Kim"]


# ---------------------------------------------------------------------------
# Reference-list and journal-name guards in extract_authors_from_markdown
# ---------------------------------------------------------------------------


class TestLooksLikeReferenceList:
    def test_lastname_initial_majority_is_reference(self):
        names = ["Hu M", "Li Y", "Jiang H", "Ge N", "Williams RS", "Yang JJ"]
        assert _looks_like_reference_list(names)

    def test_byline_format_is_not_reference(self):
        # "M. Hu, Y. Li" byline shape — initial first, period — must NOT
        # be flagged as reference-list shape.
        names = ["M. Hu", "Y. Li", "H. Jiang", "N. Ge"]
        assert not _looks_like_reference_list(names)

    def test_mixed_names_not_majority(self):
        # Some lastname-initial entries can coexist with proper bylines
        # (a paper with both formats interleaved). Only flag when the
        # SHAPE dominates.
        names = ["Jane Smith", "Bob Brown", "Hu M", "Williams RS"]
        assert not _looks_like_reference_list(names)

    def test_empty_list_not_reference(self):
        assert not _looks_like_reference_list([])


class TestLooksLikeJournalName:
    def test_acs_nano_flagged(self):
        assert _looks_like_journal_name("ACS Nano")

    def test_adv_mater_flagged(self):
        assert _looks_like_journal_name("Adv. Mater")
        assert _looks_like_journal_name("Adv. Mater. Interfaces")
        assert _looks_like_journal_name("Adv. Funct. Mater")

    def test_nat_commun_flagged(self):
        assert _looks_like_journal_name("Nat. Commun")
        assert _looks_like_journal_name("Nat. Electron")

    def test_real_author_not_flagged(self):
        # The audit revealed my old regex flagged "J. Joshua Yang" as a
        # journal — that's the false positive we need to avoid.
        assert not _looks_like_journal_name("J. Joshua Yang")
        assert not _looks_like_journal_name("J. J. Yang")
        assert not _looks_like_journal_name("R. Stanley Williams")
        assert not _looks_like_journal_name("J. P. Strachan")
        assert not _looks_like_journal_name("Sungjun Kim")
        assert not _looks_like_journal_name("Tianyu Wang")
        assert not _looks_like_journal_name("Bernabé Linares-Barranco")

    def test_empty_not_flagged(self):
        assert not _looks_like_journal_name("")


class TestExtractAuthorsRejectsReferenceLists:
    """The real failure pattern in the corpus: when the filename
    surname appears in a reference list (e.g. "... Yang JJ, Xia Q ..."),
    the OLD ``extract_authors_from_markdown`` happily returned that
    16-name list as the paper's authors. The fix prefers the EARLIEST
    matching candidate, rejects reference-list shape, and drops journal
    names."""

    def test_real_byline_wins_over_reference_list(self):
        md = (
            "# A Paper\n"
            "\n"
            "Jane Smith, Bob Brown, and Charlie Yang\n"  # real byline
            "\n"
            "## Introduction\n"
            "We build on prior work [1].\n"
            "\n"
            "## References\n"
            "[1] Hu M, Li Y, Jiang H, Ge N, Williams RS, Yang JJ. "
            "ACS Nano 2018.\n"
        )
        names = extract_authors_from_markdown(md, fn_author="Yang")
        # Must pick the real byline, NOT the reference entry.
        assert "Charlie Yang" in names
        assert "Hu M" not in names
        assert "Williams RS" not in names

    def test_journal_name_dropped(self):
        # An XMP / Info string can include a trailing journal token.
        # The post-sanitation should drop it via _looks_like_journal_name.
        names = [
            "Jane Smith", "Bob Brown", "ACS Nano",
        ]
        cleaned = [
            n for n in names
            if not _looks_like_journal_name(n)
        ]
        assert cleaned == ["Jane Smith", "Bob Brown"]
