"""Unit tests for ``wikify_simple.ingest.parsers._sections.toc_spans``.

Verifies the PDF-TOC-driven section index path the PDF parser uses
when ``doc.get_toc()`` returns >= 3 entries.
"""

from wikify_simple.ingest.parsers._sections import (
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
