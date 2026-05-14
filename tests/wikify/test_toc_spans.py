"""Unit tests for ``wikify.ingest.parsers._sections.toc_spans``.

Verifies the PDF-TOC-driven section index path the PDF parser uses
when ``doc.get_toc()`` returns >= 3 entries.
"""

from wikify.ingest.parsers._sections import (
    _clean_toc_title,
    section_spans,
    toc_spans,
)


def test_clean_toc_title_strips_zero_width():
    raw = "\ufeff\ufeff1.\u2002\ufeffIntroduction"
    assert _clean_toc_title(raw) == "1. Introduction"


def test_clean_toc_title_collapses_whitespace():
    assert _clean_toc_title("   Multiple\u00a0\u00a0\u2002Spaces   ") == "Multiple Spaces"


def test_clean_toc_title_empty_input():
    assert _clean_toc_title("") == ""
    assert _clean_toc_title(None) == ""  # type: ignore[arg-type]


def test_toc_spans_returns_none_when_no_entries():
    assert toc_spans("body text", []) is None


def test_toc_spans_returns_none_when_too_few_matches():
    body = "Body text with only one match: Introduction is here."
    toc = [(1, "Introduction", 1), (1, "Methods", 2), (1, "Results", 3)]
    # Only "Introduction" appears in the body → < 3 matches → None.
    assert toc_spans(body, toc) is None


def test_toc_spans_basic_three_section_paper():
    body = (
        "Introduction\n"
        "We motivate the problem.\n"
        "Methods\n"
        "We describe the apparatus.\n"
        "Results\n"
        "We present measurements.\n"
    )
    toc = [
        (1, "Introduction", 1),
        (1, "Methods", 1),
        (1, "Results", 2),
    ]
    spans = toc_spans(body, toc)
    assert spans is not None
    paths = [s[0] for s in spans]
    assert ["Introduction"] in paths
    assert ["Methods"] in paths
    assert ["Results"] in paths


def test_toc_spans_nested_levels_build_path_stack():
    body = (
        "1. Background\n"
        "Some background text.\n"
        "2. Methods\n"
        "Top of methods.\n"
        "2.1. Synthesis\n"
        "Synthesis steps.\n"
        "2.2. Characterization\n"
        "Characterisation steps.\n"
        "3. Results\n"
        "Results discussion.\n"
    )
    toc = [
        (1, "1. Background", 1),
        (1, "2. Methods", 1),
        (2, "2.1. Synthesis", 2),
        (2, "2.2. Characterization", 2),
        (1, "3. Results", 3),
    ]
    spans = toc_spans(body, toc)
    assert spans is not None
    paths = [s[0] for s in spans]
    # Synthesis should live under Methods → path stack accumulates
    assert ["2. Methods", "2.1. Synthesis"] in paths
    assert ["2. Methods", "2.2. Characterization"] in paths
    # Results pops back to top level
    assert ["3. Results"] in paths


def test_toc_spans_strips_unicode_noise_from_titles():
    body = "1. Introduction\nIntro text.\n2. Methods\nMethod text.\n3. Results\nResult text.\n"
    toc = [
        (1, "\ufeff\ufeff1.\u2002\ufeffIntroduction", 1),
        (1, "\ufeff\ufeff2.\u2002\ufeffMethods", 1),
        (1, "\ufeff\ufeff3.\u2002\ufeffResults", 2),
    ]
    spans = toc_spans(body, toc)
    assert spans is not None
    titles = [s[0][0] for s in spans if s[0] != ["preamble"]]
    # All three TOC titles must resolve cleanly (no leftover U+FEFF)
    for title in titles:
        assert "\ufeff" not in title
        assert "\u2002" not in title


def test_toc_spans_preamble_when_first_title_not_at_zero():
    body = "Front matter text.\n\nIntroduction\nIntro body.\nMethods\nM body.\nResults\nR body.\n"
    toc = [(1, "Introduction", 1), (1, "Methods", 1), (1, "Results", 1)]
    spans = toc_spans(body, toc)
    assert spans is not None
    # First span should be the preamble (everything before "Introduction")
    assert spans[0][0] == ["preamble"]
    assert spans[0][1] == 0


def test_section_spans_fallback_when_no_headings():
    """``section_spans`` returns a single ``["body"]`` span when the
    markdown has no ``#+`` headings — used as the toc_spans fallback."""
    body = "Just some prose with no markdown headings."
    spans = section_spans(body)
    assert spans == [(["body"], 0, len(body))]


def test_section_spans_basic_heading_tree():
    body = "# Top\nSome intro.\n## Sub\nSubsection text.\n## Sub2\nMore text.\n"
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    assert ["Top"] in paths
    assert ["Top", "Sub"] in paths
    assert ["Top", "Sub2"] in paths


# ---------------------------------------------------------------------------
# Publisher-boilerplate heading filter
# ---------------------------------------------------------------------------


def test_section_spans_drops_boilerplate_headings():
    """Pure publisher boilerplate (``OPEN ACCESS``, ``KEYWORDS``,
    ``CITATION``, ``COPYRIGHT``) must not become its own section path.
    The boilerplate's body still gets emitted under the surrounding
    real section so content isn't lost.
    """
    body = (
        "# 1. Introduction\n"
        "Intro text.\n"
        "## KEYWORDS\n"
        "memristor, ALD\n"
        "## 2. Methods\n"
        "Method text.\n"
        "## CITATION\n"
        "Cite this.\n"
        "## 3. Results\n"
        "Result text.\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    # Real sections survive.
    assert ["1. Introduction"] in paths
    assert any("2. Methods" in p for p in paths)
    assert any("3. Results" in p for p in paths)
    # Boilerplate headings DO NOT introduce a section path entry.
    for p in paths:
        assert "KEYWORDS" not in p
        assert "CITATION" not in p


def test_section_spans_keeps_acknowledgments_for_classification():
    """``Acknowledgments`` MUST remain in the section_path so
    ``classify_section_path`` returns ACKNOWLEDGMENTS — otherwise
    ``exclude_kinds=['acknowledgments']`` queries leak the chunks
    through."""
    body = (
        "# 1. Introduction\n"
        "Intro text.\n"
        "## Acknowledgments\n"
        "We thank...\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    assert any("Acknowledgments" in p for p in paths)


def test_section_spans_keeps_content_bearing_sections():
    """``Author contributions``, ``Data availability``, ``Funding``,
    ``Conflict of interest`` carry per-paper info researchers query
    directly. They must survive as named sections so they remain
    locatable downstream."""
    body = (
        "# 1. Introduction\n"
        "Intro.\n"
        "## Author contributions\n"
        "A.B. did X.\n"
        "## Data availability\n"
        "Public on Zenodo.\n"
        "## Funding\n"
        "NSF grant 12345.\n"
        "## Conflict of interest\n"
        "None declared.\n"
        "## Supporting information\n"
        "Available online.\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    for keeper in (
        "Author contributions",
        "Data availability",
        "Funding",
        "Conflict of interest",
        "Supporting information",
    ):
        assert any(keeper in p for p in paths), keeper


def test_section_spans_drops_publisher_tags():
    """``OPEN ACCESS``, ``*CORRESPONDENCE``, ``CITATION``, ``COPYRIGHT``,
    ``Publisher's note`` are all publisher template tags. Drop them."""
    body = (
        "# OPEN ACCESS\n"
        "Open access info.\n"
        "# *CORRESPONDENCE\n"
        "name@example.com\n"
        "# CITATION\n"
        "Cite this.\n"
        "# COPYRIGHT\n"
        "(c) 2024.\n"
        "# Publisher's note\n"
        "Note.\n"
        "# 1. Introduction\n"
        "Real intro.\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    assert ["1. Introduction"] in paths
    # None of the boilerplate names appear.
    flat = [p for path in paths for p in path]
    for noise in ("OPEN ACCESS", "*CORRESPONDENCE", "CITATION",
                  "COPYRIGHT", "Publisher's note"):
        assert noise not in flat


def test_section_spans_drops_url_headings():
    """URLs and DOIs lifted as headings (``# https://doi.org/...``) are
    boilerplate, not document sections."""
    body = (
        "# https://doi.org/10.1234/example\n"
        "DOI line.\n"
        "# 1. Introduction\n"
        "Intro.\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    assert ["1. Introduction"] in paths
    flat = [p for path in paths for p in path]
    assert not any("doi.org" in p for p in flat)


def test_section_spans_keeps_real_sections_with_similar_names():
    """Real numbered sections must survive even if their name overlaps
    with a boilerplate keyword (``Acknowledgments`` is in the
    boilerplate set, but a section ``5. Conclusions and
    Acknowledgments`` should still pass — the boilerplate filter
    matches the WHOLE heading, not substrings)."""
    body = (
        "# 1. Introduction\n"
        "Text.\n"
        "# 5. Conclusions and Acknowledgments\n"
        "Combined section.\n"
    )
    spans = section_spans(body)
    paths = [s[0] for s in spans]
    assert ["5. Conclusions and Acknowledgments"] in paths
