"""Tests for ingest/citations.py."""

from wikify.ingest.citations import (
    _extract_author_last_names,
    _find_refs_section,
    extract_citations,
)

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
    assert "raw_text" in first
    assert "Strukov" in first["raw_text"]


def test_doi_extracted():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    assert cits[0]["doi"] == "10.1038/nature06932"


def test_no_section_returns_empty():
    assert extract_citations("# title\n\nbody only", "doc-x") == []


def test_dict_shape():
    """New schema: ord, raw_text, year, doi, author_last_names."""
    cits = extract_citations(SAMPLE_MD, "doc-1")
    expected_keys = {"ord", "raw_text", "year", "doi", "author_last_names"}
    for c in cits:
        assert expected_keys.issubset(c.keys())
    # Should NOT have structured fields (those come from CrossRef)
    assert "title" not in cits[0]
    assert "venue" not in cits[0]
    assert "authors" not in cits[0]


def test_author_last_names_extracted():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    first = cits[0]
    names = first["author_last_names"]
    assert "Strukov" in names
    assert "Snider" in names


def test_extract_author_last_names_filters_venue_words():
    raw = "G. Kresse; Furthmuller, J. Phys. Rev. B 1996, 54, 11169."
    names = _extract_author_last_names(raw)
    assert "Kresse" in names
    assert "Furthmuller" in names
    # Venue words should be filtered
    assert "Rev" not in names


def test_acs_reference_extracts_year_and_doi():
    md = "\n".join(
        [
            "# Paper",
            "",
            "## References",
            "",
            "[1] Kresse, G.; Furthmuller, J. Efficient Iterative Schemes for Ab "
            "Initio Total-Energy Calculations Using a Plane-Wave Basis Set. "
            "Phys. Rev. B: Condens. Matter Mater. Phys. 1996, 54 (16), "
            "11169-11186.",
        ]
    )
    cits = extract_citations(md, "doc-1")
    assert cits[0]["year"] == 1996
    assert "Kresse" in cits[0]["author_last_names"]
