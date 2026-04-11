"""Tests for ingest/citations.py."""

from wikify_simple.ingest.citations import _find_refs_section, extract_citations

SAMPLE_MD = "\n".join(
    [
        "# A Paper",
        "",
        "Some body text discussing things.",
        "",
        "## References",
        "",
        "[1] Strukov, D. B., Snider, G. S., Stewart, D. R., & Williams, R. S. "
        "(2008). The missing memristor found. Nature, 453(7191), 80-83. "
        "doi:10.1038/nature06932",
        "",
        "[2] Mead, C. (1990). Neuromorphic electronic systems. Proceedings "
        "of the IEEE, 78(10), 1629-1636.",
        "",
        "[3] Indiveri, G., & Liu, S. C. (2015). Memory and information "
        "processing in neuromorphic systems. Proceedings of the IEEE, "
        "103(8), 1379-1397.",
        "",
    ]
)


def test_find_refs_section_detected():
    section = _find_refs_section(SAMPLE_MD)
    assert section is not None
    assert "Strukov" in section


def test_extract_three_entries():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    assert len(cits) == 3
    first = cits[0]
    assert first["ord"] == 0
    assert first["year"] == 2008
    assert any("Strukov" in a for a in first["authors"])
    assert "missing memristor" in first["title"].lower()


def test_doi_extracted():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    assert cits[0]["doi"] == "10.1038/nature06932"


def test_no_section_returns_empty():
    assert extract_citations("# title\n\nbody only", "doc-x") == []


def test_dict_shape():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    expected_keys = {"ord", "raw_text", "authors", "year", "title", "venue", "doi"}
    for c in cits:
        assert expected_keys.issubset(c.keys())
