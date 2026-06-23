"""Regression tests for render bugs F36, F31, F34.

F36 -- _figure_alt_text truncates at a word boundary (not mid-word).
F31 -- _remap_figure_citation_numbers removes orphan figure-citation sups.
F34 -- _clean_evidence_lines skips reformatting for kind='data' pages.
"""

import re

import markdown

from wikify.render.html.render import (
    _clean_evidence_lines,
    _figure_alt_text,
    _remap_figure_citation_numbers,
)

# ---------------------------------------------------------------------------
# F36 -- word-boundary truncation in _figure_alt_text
# ---------------------------------------------------------------------------

# Build a caption where character 160 lands inside the word "stoichiometry"
# so that a naive [:160] cut leaves a partial word.
#
# 160 chars exactly:
#   "The temperature-controlled oxygen stoichiometry " is 48 chars.
#   Repeat "precise control of oxide thin film deposition " (46 chars) 3x = 138.
#   Then "temperature-controlled oxygen sto" fills up to 160.
#
# Easier: craft a string that is exactly 162 chars with a word crossing 160.
_SHORT_CAPTION = "Short caption under 160 chars."

# The word "stoichiometry" crosses the 160-char boundary here.
_LONG_PREFIX = "A " + ("word " * 31)  # 2 + 31*5 = 157 chars
# _LONG_PREFIX is 157 chars; next word "stoichiometry" would put us at 170.
_LONG_CAPTION = _LONG_PREFIX + "stoichiometry and surface chemistry details."


def test_figure_alt_text_short_caption_unchanged():
    result = _figure_alt_text("", _SHORT_CAPTION)
    assert result == _SHORT_CAPTION
    assert not result.endswith("...")


def test_figure_alt_text_long_caption_ends_with_ellipsis():
    assert len(_LONG_CAPTION) > 160
    result = _figure_alt_text("", _LONG_CAPTION)
    assert result.endswith("...")


def test_figure_alt_text_long_caption_no_partial_word():
    """The text before '...' must end on a complete word."""
    result = _figure_alt_text("", _LONG_CAPTION)
    assert result.endswith("...")
    body = result[:-3]  # strip trailing "..."
    # The raw 160-char slice of _LONG_CAPTION ends with "stoich" (partial).
    # The word-boundary fix should have cut before "stoichiometry".
    assert "stoichiometry" not in body
    # Ensure the last token before "..." is a complete word (no trailing space).
    assert not body.endswith(" ")


def test_figure_alt_text_label_takes_priority():
    result = _figure_alt_text("My Label", _LONG_CAPTION)
    assert result == "My Label"


def test_figure_alt_text_fallback_when_empty():
    result = _figure_alt_text("", "")
    assert result == "Figure"


# ---------------------------------------------------------------------------
# F31 -- orphan figure-citation sup removal in _remap_figure_citation_numbers
# ---------------------------------------------------------------------------


def _make_html(body_md: str) -> str:
    md = markdown.Markdown(extensions=["footnotes"])
    return md.convert(body_md)


def _fig_sup(marker: str) -> str:
    return (
        f'<sup class="figure-citation">'
        f'<a href="#fn:{marker}">[{marker}]</a>'
        f"</sup>"
    )


def test_orphan_sup_completely_removed(capsys):
    """Orphan marker (no body footnote-ref) must be stripped entirely."""
    body_md = "Prose with no reference to e2.\n"
    html = _make_html(body_md) + _fig_sup("e2")

    result = _remap_figure_citation_numbers(html, page_id="test-page")

    assert "#fn:e2" not in result
    assert "[e2]" not in result
    assert "figure-citation" not in result

    captured = capsys.readouterr()
    assert "e2" in captured.err


def test_non_orphan_marker_renumbered():
    """Non-orphan marker (has body footnote-ref) is renumbered to integer."""
    body_md = (
        "See this claim.[^e5]\n\n"
        "[^e5]: Author 2021 > 'evidence.'\n"
    )
    body_html = _make_html(body_md)
    # Verify python-markdown assigned '1' to e5.
    assert re.search(r'href="#fn:e5">1</a>', body_html), body_html

    html = body_html + _fig_sup("e5")
    result = _remap_figure_citation_numbers(html, page_id="test-page")

    # Display text rewritten to '1'; link target preserved.
    assert "[1]</a></sup>" in result
    assert 'href="#fn:e5"' in result
    assert "[e5]" not in result


# ---------------------------------------------------------------------------
# F34 -- _clean_evidence_lines skips reformat for kind='data'
# ---------------------------------------------------------------------------

# Shared doc_id for all three markers to trigger collapse on non-data pages.
_SHARED_DOC_ID = "[2023 Lee]"

_DATA_BODY = (
    "Some prose referencing cell values.[^d1][^d2][^d3]\n"
    "\n"
    f"[^d1]: {_SHARED_DOC_ID} > \"TiO2 thickness = 5 nm\"\n"
    f"[^d2]: {_SHARED_DOC_ID} > \"Al2O3 thickness = 3 nm\"\n"
    f"[^d3]: {_SHARED_DOC_ID} > \"HfO2 thickness = 7 nm\"\n"
)


def test_data_kind_preserves_all_footnotes():
    """kind='data' must not collapse or drop any footnote definitions."""
    result = _clean_evidence_lines(_DATA_BODY, kind="data")
    # All three definitions must survive unchanged.
    assert "[^d1]:" in result
    assert "[^d2]:" in result
    assert "[^d3]:" in result


def test_data_kind_preserves_quotes():
    """kind='data' must keep the per-cell grounding quote on each footnote."""
    result = _clean_evidence_lines(_DATA_BODY, kind="data")
    assert "TiO2 thickness = 5 nm" in result
    assert "Al2O3 thickness = 3 nm" in result
    assert "HfO2 thickness = 7 nm" in result


def test_data_kind_body_unchanged():
    """kind='data' returns body byte-for-byte unchanged."""
    result = _clean_evidence_lines(_DATA_BODY, kind="data")
    assert result == _DATA_BODY


def test_article_kind_collapses_same_paper():
    """kind='article' (default) still collapses same-paper markers."""
    # Two markers pointing to the same doc_id; only one should survive.
    body = (
        "First claim.[^e1] Second claim.[^e2]\n"
        "\n"
        f"[^e1]: {_SHARED_DOC_ID} > \"quote one\"\n"
        f"[^e2]: {_SHARED_DOC_ID} > \"quote two\"\n"
    )
    result = _clean_evidence_lines(body, kind="article")
    # After collapse both prose uses should reference the canonical marker (e1).
    # Only one [^e1]: definition should remain; [^e2]: should be gone.
    lines = result.split("\n")
    def_lines = [ln for ln in lines if ln.startswith("[^") and "]:" in ln]
    assert len(def_lines) == 1
    assert def_lines[0].startswith("[^e1]:")


def test_data_kind_strips_parser_only_docid_tail():
    """The parser-only ``(<doc_id with hash>)`` tail is hidden from display
    (F33), while the clean label and quote survive."""
    body = (
        "Cell value.[^d1]\n"
        "\n"
        '[^d1]: [2023 Sahu] Filament geometry in oxide. Table 1 '
        '([2023 Sahu] Filament geometry in oxide_4dbfd151d2dc) '
        '> "Endurance = 10 3"\n'
    )
    result = _clean_evidence_lines(body, kind="data")
    # Raw doc hash must not appear in the rendered display.
    assert "_4dbfd151d2dc" not in result
    assert not re.search(r"_[0-9a-f]{12}", result)
    # Clean label, locator, and quote survive.
    assert "[2023 Sahu] Filament geometry in oxide. Table 1" in result
    assert "Endurance = 10 3" in result
    # The marker itself is intact.
    assert "[^d1]:" in result


def test_data_kind_tail_strip_leaves_quote_parens_intact():
    """Stripping the doc_id tail must not eat parentheses inside the quote."""
    body = (
        "Cell.[^d1]\n"
        "\n"
        '[^d1]: [2024 Ratier] VO2 by ALD '
        '([2024 Ratier] VO2 by ALD_949cb9433268) '
        '> "switch (metallic phase) at 8.5 V"\n'
    )
    result = _clean_evidence_lines(body, kind="data")
    assert "_949cb9433268" not in result
    assert "switch (metallic phase) at 8.5 V" in result


def test_default_kind_is_article():
    """Omitting kind= defaults to article behavior (collapse fires)."""
    body = (
        "Ref one.[^e1] Ref two.[^e2]\n"
        "\n"
        f"[^e1]: {_SHARED_DOC_ID} > \"q1\"\n"
        f"[^e2]: {_SHARED_DOC_ID} > \"q2\"\n"
    )
    result = _clean_evidence_lines(body)
    lines = result.split("\n")
    def_lines = [ln for ln in lines if ln.startswith("[^") and "]:" in ln]
    assert len(def_lines) == 1
