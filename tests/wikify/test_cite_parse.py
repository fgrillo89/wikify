"""Tests for citestore.parse -- standalone citation text parser."""

from wikify.citestore.parse import (
    citation_fingerprint,
    detect_format,
    fuse_cross_paper_evidence,
    parse_citation,
)

# ---- Format detection ----


def test_detect_quoted():
    raw = 'H. Wong et al., "Metal-oxide RRAM," Proc. IEEE, vol. 100, 2012.'
    assert detect_format(raw) == "quoted"


def test_detect_perioded():
    raw = (
        "Chua, L. O. Memristor - the missing circuit element."
        " IEEE Trans. 18, 507 (1971)."
    )
    assert detect_format(raw) == "perioded"


def test_detect_apa():
    raw = (
        "Smith, J. A. (2020). Title of the paper."
        " Journal of Testing, 42(3), 100-110."
    )
    assert detect_format(raw) == "apa"


def test_detect_acs():
    raw = (
        "Grillo, F.; van Ommen, J. R. Title of Article."
        " J. Name 2020, 12 (3), 45-67."
    )
    assert detect_format(raw) == "acs"


# ---- Title extraction ----


def test_title_ieee():
    raw = (
        'S. R. Ovshinsky, "Reversible electrical switching phenomena'
        ' in disordered structures," Phy. Rev. Lett., vol. 21, 1968.'
    )
    result = parse_citation(raw)
    assert "Reversible electrical switching" in result.get("title", "")


def test_title_nature():
    raw = (
        "Chua, L. O. Memristor - the missing circuit element."
        " IEEE Trans. Circuit Theory 18, 507-519 (1971)."
    )
    result = parse_citation(raw, year=1971)
    assert result["title"] == "Memristor - the missing circuit element"


def test_title_nature_multi_author():
    raw = (
        "Terabe, K., Hasegawa, T., Nakayama, T. & Aono, M."
        " Quantized conductance atomic switch. Nature 433, 47-50 (2005)."
    )
    result = parse_citation(raw, year=2005)
    assert result["title"] == "Quantized conductance atomic switch"


def test_title_apa():
    raw = (
        "Smith, J. A. (2020). Deep learning for material science"
        " applications. Journal of ML, 42(3), 100-110."
    )
    result = parse_citation(raw)
    assert "Deep learning for material science" in result.get("title", "")


# ---- Author extraction ----


def test_authors_nature():
    raw = (
        "Hickmott, M. T. Low-frequency negative resistance in thin"
        " anodic oxide films. J. Appl. Phys. 33, 2669-2682 (1962)."
    )
    result = parse_citation(raw, year=1962)
    authors = result.get("authors", [])
    assert len(authors) >= 1
    assert any("Hickmott" in a for a in authors)


def test_authors_ieee():
    raw = (
        'S. R. Ovshinsky, "Reversible switching in disordered'
        ' structures," Phy. Rev. Lett., vol. 21, 1968.'
    )
    result = parse_citation(raw)
    authors = result.get("authors", [])
    assert any("Ovshinsky" in a for a in authors)


# ---- Venue extraction ----


def test_venue_nature():
    raw = (
        "Terabe, K., Hasegawa, T. & Aono, M."
        " Quantized conductance atomic switch. Nature 433, 47-50 (2005)."
    )
    result = parse_citation(raw, year=2005)
    assert "Nature" in result.get("venue", "")


def test_volume_and_pages():
    raw = (
        "Chua, L. O. Memristor - the missing circuit element."
        " IEEE Trans. Circuit Theory 18, 507-519 (1971)."
    )
    result = parse_citation(raw, year=1971)
    assert result.get("pages") == "507--519"


# ---- Short/garbage input ----


def test_short_input():
    assert not parse_citation("too short").get("title")


def test_empty_input():
    assert parse_citation("") == {}


# ---- Fingerprint ----


def test_fingerprint_doi():
    assert citation_fingerprint({"doi": "10.1234/test"}) == "doi:10.1234/test"


def test_fingerprint_author_year():
    fp = citation_fingerprint({
        "author_last_names": ["Smith", "Jones"], "year": 2020,
    })
    assert "smith" in fp
    assert "2020" in fp


def test_fingerprint_empty():
    assert citation_fingerprint({}) == ""


# ---- Cross-paper fusion ----


def test_fusion_fills_missing_fields():
    cits_a = [{
        "raw_text": "Smith 2020", "doi": "10.1234/x",
        "title": "Full Title Here", "year": 2020, "author_last_names": [],
    }]
    cits_b = [{
        "raw_text": "Smith 2020 short", "doi": "10.1234/x",
        "year": 2020, "venue": "Nature", "author_last_names": [],
    }]
    fuse_cross_paper_evidence([cits_a, cits_b])
    assert cits_b[0].get("title") == "Full Title Here"
    assert cits_a[0].get("venue") == "Nature"
