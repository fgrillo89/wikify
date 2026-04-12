"""Unit tests for ``wikify.ingest.figure_refs``."""

from wikify.ingest.figure_refs import extract_figure_refs


def test_empty_returns_empty():
    assert extract_figure_refs("") == []


def test_simple_figure_caption():
    md = "## Results\n\nFig. 1. Schematic of the device cross section.\n\nMore text."
    refs = extract_figure_refs(md)
    assert len(refs) == 1
    assert refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 1
    assert refs[0]["sub"] == ""
    assert "Schematic" in refs[0]["caption"]


def test_figure_with_subletter():
    md = "Fig. 2a. Histogram of switching voltages."
    refs = extract_figure_refs(md)
    assert len(refs) == 1
    assert refs[0]["num"] == 2
    assert refs[0]["sub"] == "a"


def test_table_caption():
    md = "Table 3. Summary of measured device parameters."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "table"
    assert refs[0]["num"] == 3


def test_scheme_caption():
    md = "Scheme 1. Synthesis route for the iron oxide precursor."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "scheme"
    assert refs[0]["num"] == 1


def test_bold_wrapped_caption():
    md = "**Fig. 4.** Time evolution of the resistance state under pulse trains."
    refs = extract_figure_refs(md)
    assert refs
    assert refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 4
    assert "Time evolution" in refs[0]["caption"]


def test_section_path_anchored_at_caption():
    md = (
        "# Paper\n\n"
        "## Methods\n\n"
        "Some method description.\n\n"
        "Fig. 1. Apparatus diagram.\n\n"
        "## Results\n\n"
        "Fig. 2. Output curves.\n"
    )
    refs = extract_figure_refs(md)
    by_num = {r["num"]: r for r in refs}
    assert by_num[1]["section_path"] == ["Paper", "Methods"]
    assert by_num[2]["section_path"] == ["Paper", "Results"]


def test_dedup_keeps_first_occurrence():
    md = (
        "Fig. 1. First mention with caption text.\n\n"
        "Body referencing Fig. 1 again later.\n\n"
        "Fig. 1. Duplicate caption that should NOT replace the first.\n"
    )
    refs = extract_figure_refs(md)
    fig1 = [r for r in refs if r["kind"] == "figure" and r["num"] == 1]
    assert len(fig1) == 1
    assert "First mention" in fig1[0]["caption"]


def test_inline_body_reference_not_treated_as_caption():
    """A bare 'as shown in Fig. 1' inside a paragraph must not be parsed
    as a new caption — only line-leading caption patterns count."""
    md = "We attribute the effect to the field gradient as shown in Fig. 1 above."
    refs = extract_figure_refs(md)
    # No leading-line "Fig. N. caption" pattern → no captions extracted.
    # (The `as shown in Fig. 1` lacks the period+space caption marker.)
    assert all(not r["caption"].startswith("above") for r in refs)


def test_caption_truncated_at_500_chars():
    long_caption = "x " * 400  # 800 chars
    md = f"Fig. 1. {long_caption}"
    refs = extract_figure_refs(md)
    assert refs and len(refs[0]["caption"]) <= 500


def test_offset_sorted():
    md = (
        "Scheme 1. First.\n\n"
        "Fig. 1. Second.\n\n"
        "Table 1. Third.\n"
    )
    refs = extract_figure_refs(md)
    offsets = [r["char_offset"] for r in refs]
    assert offsets == sorted(offsets)


def test_handles_multiple_kinds_same_doc():
    md = (
        "Fig. 1. Diagram of the experimental setup.\n\n"
        "Table 1. Sample list.\n\n"
        "Fig. 2. Resulting traces.\n\n"
        "Scheme 1. Reaction mechanism.\n"
    )
    refs = extract_figure_refs(md)
    keys = {(r["kind"], r["num"]) for r in refs}
    assert ("figure", 1) in keys
    assert ("figure", 2) in keys
    assert ("table", 1) in keys
    assert ("scheme", 1) in keys


def test_case_insensitive_figure():
    """FIGURE, figure, and Fig should all match."""
    md = "FIGURE 1. Uppercase caption.\n\nfigure 2. Lowercase caption."
    refs = extract_figure_refs(md)
    nums = {r["num"] for r in refs}
    assert 1 in nums
    assert 2 in nums


def test_tab_abbreviation():
    """Tab. N should match as table kind."""
    md = "Tab. 1. Abbreviation of Table."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "table"
    assert refs[0]["num"] == 1


def test_illustration_caption():
    md = "Illustration 1. Overview of the experimental setup."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 1


def test_schematic_caption():
    md = "Schematic 1. Cross-section of the thin film stack."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 1


def test_graphical_abstract():
    md = "Graphical Abstract. Summary of the synthesis process."
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 0
    assert "Summary" in refs[0]["caption"]


def test_graphical_abstract_no_separator():
    """Graphical Abstract alone on a line (no caption separator)."""
    md = "## Abstract\n\nGraphical Abstract"
    refs = extract_figure_refs(md)
    assert refs and refs[0]["kind"] == "figure"
    assert refs[0]["num"] == 0
