"""Regression tests for figure-citation number remapping.

Verifies that ``_remap_figure_citation_numbers`` rewrites ``[eN]`` display
text in figure-citation sups to the integer assigned by python-markdown's
footnotes extension, while keeping the href target intact.
"""

import re

import markdown

from wikify.render.html.render import _remap_figure_citation_numbers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_body_html(body_md: str) -> str:
    """Convert markdown to HTML with the same footnotes extension the renderer uses."""
    md = markdown.Markdown(extensions=["footnotes"])
    return md.convert(body_md)


def _inject_figure_caption(html: str, marker: str) -> str:
    """Append a synthetic figure block carrying a figure-citation sup."""
    return (
        html
        + f'\n<figure class="wiki-figure">'
        f'<img src="x.png" alt="fig">'
        f'<figcaption>A figure.'
        f'<sup class="figure-citation">'
        f'<a href="#fn:{marker}">[{marker}]</a>'
        f"</sup></figcaption>"
        f"</figure>\n"
    )


# ---------------------------------------------------------------------------
# Normal case: marker cited in body prose -> caption shows integer
# ---------------------------------------------------------------------------


def test_figure_citation_remapped_to_integer():
    body_md = (
        "Deposited layers show excellent conformality.[^e3]\n\n"
        "[^e3]: Smith 2020 > 'ALD produces conformal films.'\n"
    )
    body_html = _make_body_html(body_md)

    # Verify python-markdown assigned '1' to e3.
    assert re.search(r'href="#fn:e3">1</a>', body_html), body_html

    combined_html = _inject_figure_caption(body_html, "e3")
    result = _remap_figure_citation_numbers(combined_html, page_id="test-page")

    # Display text should be numeric '1', not raw 'e3'.
    assert "[1]" in result
    assert "[e3]" not in result
    # Link target must still point to fn:e3.
    assert 'href="#fn:e3"' in result


def test_figure_citation_link_target_preserved():
    """href is unchanged even after display text is rewritten."""
    body_md = (
        "First ref.[^e1] Second ref.[^e2]\n\n"
        "[^e1]: Doc A > 'quote A.'\n"
        "[^e2]: Doc B > 'quote B.'\n"
    )
    body_html = _make_body_html(body_md)
    combined_html = _inject_figure_caption(body_html, "e2")
    result = _remap_figure_citation_numbers(combined_html)

    # e2 should map to '2' (second footnote in order).
    assert '[2]</a></sup>' in result
    assert 'href="#fn:e2"' in result


# ---------------------------------------------------------------------------
# Orphan marker: cited only in figure, never in prose
# ---------------------------------------------------------------------------


def test_orphan_marker_keeps_raw_display(capsys):
    """A figure-only marker keeps [eN] display and emits a warning."""
    body_md = (
        "Text with one footnote.[^e3]\n\n"
        "[^e3]: Smith 2020 > 'ALD films.'\n"
    )
    body_html = _make_body_html(body_md)
    # Inject a figure using e9, which has no body reference.
    combined_html = _inject_figure_caption(body_html, "e9")
    result = _remap_figure_citation_numbers(combined_html, page_id="ald-page")

    # Raw marker preserved in display (link still works).
    assert "[e9]" in result
    assert 'href="#fn:e9"' in result

    # Warning emitted to stderr.
    captured = capsys.readouterr()
    assert "e9" in captured.err
    assert "ald-page" in captured.err


def test_orphan_marker_no_false_positive_for_mapped_markers(capsys):
    """No warning emitted for markers that do have body refs."""
    body_md = (
        "See figure.[^e3]\n\n"
        "[^e3]: Doc > 'quote.'\n"
    )
    body_html = _make_body_html(body_md)
    combined_html = _inject_figure_caption(body_html, "e3")
    _remap_figure_citation_numbers(combined_html)
    captured = capsys.readouterr()
    assert "e3" not in captured.err


# ---------------------------------------------------------------------------
# Multiple figures, multiple markers
# ---------------------------------------------------------------------------


def test_multiple_figures_all_remapped():
    body_md = (
        "First[^e1] and second[^e2] references.\n\n"
        "[^e1]: DocA > 'A.'\n"
        "[^e2]: DocB > 'B.'\n"
    )
    body_html = _make_body_html(body_md)
    html = _inject_figure_caption(body_html, "e1")
    html = _inject_figure_caption(html, "e2")
    result = _remap_figure_citation_numbers(html)

    assert "[1]" in result
    assert "[2]" in result
    assert "[e1]" not in result
    assert "[e2]" not in result
