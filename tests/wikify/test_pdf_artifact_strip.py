"""Tests for `_strip_pdf_artifacts` — the pymupdf4llm output scrubber."""

from wikify.ingest.parsers.pdf import _strip_pdf_artifacts


def test_inline_citation_markers_stripped():
    s = "The memristor [12] was described by Chua [1-3] in 1971."
    # Citation removal leaves double spaces; subsequent collapse yields single spaces.
    assert _strip_pdf_artifacts(s) == "The memristor was described by Chua in 1971."


def test_bracket_wrap_unwrapped():
    s = "H][2][plasma] was used."
    out = _strip_pdf_artifacts(s)
    # 'H]' stays (single char), '[2]' is a digit-run bracket and unwraps
    # to '2', '[plasma]' unwraps to 'plasma'.
    assert "plasma" in out
    assert "[plasma]" not in out


def test_subfigure_refs_preserved():
    # [a] and [b] are one character and must NOT unwrap.
    s = "See Figure 1[a] and 1[b] for details."
    out = _strip_pdf_artifacts(s)
    assert "[a]" in out
    assert "[b]" in out


def test_figure_n_refs_preserved():
    # [Figure 1] contains a space so the regex can't match it.
    s = "As shown in [Figure 1] of the paper."
    assert _strip_pdf_artifacts(s) == s


def test_dashes_normalized():
    s = "self\u2013limiting \u2014 conformal\u2212film"
    assert _strip_pdf_artifacts(s) == "self-limiting - conformal-film"


def test_double_space_collapsed_newlines_preserved():
    s = "foo    bar\n\nbaz  qux"
    out = _strip_pdf_artifacts(s)
    assert out == "foo bar\n\nbaz qux"


def test_empty_input():
    assert _strip_pdf_artifacts("") == ""
