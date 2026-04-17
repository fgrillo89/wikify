"""Tests for ingest/citations.py."""

from wikify.ingest.citations import (
    _extract_author_last_names,
    _extract_doi,
    _find_refs_section,
    extract_citations,
    repair_doi,
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
    assert first.ord == 0
    assert first.year == 2008
    assert first.raw_text
    assert "Strukov" in first.raw_text


def test_doi_extracted():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    assert cits[0].doi == "10.1038/nature06932"


def test_no_section_returns_empty():
    assert extract_citations("# title\n\nbody only", "doc-x") == []


def test_citation_entry_shape():
    """CitationEntry has all expected fields populated."""
    cits = extract_citations(SAMPLE_MD, "doc-1")
    first = cits[0]
    assert hasattr(first, "ord")
    assert hasattr(first, "raw_text")
    assert hasattr(first, "year")
    assert hasattr(first, "doi")
    assert hasattr(first, "author_last_names")
    # Title/venue/authors not populated by extract_citations (filled later)
    assert first.title == ""
    assert first.venue == ""
    assert first.authors == []


def test_author_last_names_extracted():
    cits = extract_citations(SAMPLE_MD, "doc-1")
    first = cits[0]
    names = first.author_last_names
    assert "Strukov" in names
    assert "Snider" in names


def test_extract_author_last_names_filters_venue_words():
    raw = "G. Kresse; Furthmuller, J. Phys. Rev. B 1996, 54, 11169."
    names = _extract_author_last_names(raw)
    assert "Kresse" in names
    assert "Furthmuller" in names
    # Venue words should be filtered
    assert "Rev" not in names


def test_doi_with_balanced_parens_not_truncated():
    """Elsevier legacy DOIs carry balanced parens (Neural Netw 1997, Nat Commun)."""
    raw = (
        "W. Maass. Networks of spiking neurons: The third generation of "
        "neural network models. Neural Netw. 10, 1659 (1997). "
        "doi:10.1016/S0893-6080(97)00011-7"
    )
    assert _extract_doi(raw) == "10.1016/S0893-6080(97)00011-7"


def test_doi_trailing_unbalanced_paren_stripped():
    raw = "See reference (10.1038/nature06932)."
    assert _extract_doi(raw) == "10.1038/nature06932"


def test_doi_trailing_period_stripped():
    raw = "doi:10.1038/nature06932."
    assert _extract_doi(raw) == "10.1038/nature06932"


def test_doi_extraction_from_paren_doi_in_reference():
    """Full reference text with a DOI that has parens must survive extraction."""
    md = "\n".join(
        [
            "# Paper",
            "",
            "## References",
            "",
            "[1] W. Maass. Networks of spiking neurons. "
            "Neural Netw. 10, 1659-1671 (1997). "
            "doi:10.1016/S0893-6080(97)00011-7",
        ]
    )
    cits = extract_citations(md, "doc-x")
    assert cits[0].doi == "10.1016/S0893-6080(97)00011-7"


def test_repair_doi_replaces_truncated_with_fresh_balanced():
    raw = (
        "W. Maass. Networks of spiking neurons. Neural Netw. 10, 1659 "
        "(1997). doi:10.1016/S0893-6080(97)00011-7"
    )
    assert (
        repair_doi(raw, "10.1016/S0893-6080(97")
        == "10.1016/S0893-6080(97)00011-7"
    )


def test_repair_doi_keeps_existing_when_raw_has_no_doi():
    assert repair_doi("No DOI here.", "10.1038/nature06932") == "10.1038/nature06932"


def test_repair_doi_keeps_longer_existing():
    raw = "partial: 10.1038/nat"
    assert repair_doi(raw, "10.1038/nature06932") == "10.1038/nature06932"


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
    assert cits[0].year == 1996
    assert "Kresse" in cits[0].author_last_names
