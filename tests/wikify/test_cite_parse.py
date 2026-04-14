"""Tests for heuristic citation parsing."""

from __future__ import annotations

from wikify.ingest.cite_parse import (
    _citation_fingerprint,
    _detect_format,
    _extract_title,
    fuse_cross_paper_evidence,
    parse_citation_heuristic,
)


# ---- Format detection ----

def test_detect_ieee():
    raw = 'H. Wong et al., "Metal-oxide RRAM," Proc. IEEE, vol. 100, 2012.'
    assert _detect_format(raw) == "ieee"


def test_detect_nature():
    raw = "Chua, L. O. Memristor - the missing circuit element. IEEE Trans. 18, 507 (1971)."
    assert _detect_format(raw) == "nature"


def test_detect_apa():
    raw = "Smith, J. A. (2020). Title of the paper. Journal of Testing, 42(3), 100-110."
    assert _detect_format(raw) == "apa"


# ---- Title extraction ----

def test_title_ieee():
    cit = {"raw_text": 'S. R. Ovshinsky, "Reversible electrical switching phenomena in disordered structures," Phy. Rev. Lett., vol. 21, 1968.'}
    parse_citation_heuristic(cit)
    assert "Reversible electrical switching" in cit.get("title", "")


def test_title_nature():
    cit = {"raw_text": "Chua, L. O. Memristor - the missing circuit element. IEEE Trans. Circuit Theory 18, 507-519 (1971).", "year": 1971}
    parse_citation_heuristic(cit)
    assert cit.get("title") == "Memristor - the missing circuit element"


def test_title_nature_multi_author():
    cit = {"raw_text": "Terabe, K., Hasegawa, T., Nakayama, T. & Aono, M. Quantized conductance atomic switch. Nature 433, 47-50 (2005).", "year": 2005}
    parse_citation_heuristic(cit)
    assert cit.get("title") == "Quantized conductance atomic switch"


def test_title_apa():
    cit = {"raw_text": "Smith, J. A. (2020). Deep learning for material science applications. Journal of ML, 42(3), 100-110."}
    parse_citation_heuristic(cit)
    assert "Deep learning for material science" in cit.get("title", "")


# ---- Author extraction ----

def test_authors_nature():
    cit = {"raw_text": "Hickmott, M. T. Low-frequency negative resistance in thin anodic oxide films. J. Appl. Phys. 33, 2669-2682 (1962).", "year": 1962}
    parse_citation_heuristic(cit)
    authors = cit.get("authors", [])
    assert len(authors) >= 1
    assert any("Hickmott" in a for a in authors)


def test_authors_ieee():
    cit = {"raw_text": 'S. R. Ovshinsky, "Reversible switching in disordered structures," Phy. Rev. Lett., vol. 21, 1968.'}
    parse_citation_heuristic(cit)
    authors = cit.get("authors", [])
    assert any("Ovshinsky" in a for a in authors)


# ---- Venue extraction ----

def test_venue_nature():
    cit = {"raw_text": "Terabe, K., Hasegawa, T. & Aono, M. Quantized conductance atomic switch. Nature 433, 47-50 (2005).", "year": 2005}
    parse_citation_heuristic(cit)
    assert "Nature" in cit.get("venue", "")


def test_volume_and_pages():
    cit = {"raw_text": "Chua, L. O. Memristor - the missing circuit element. IEEE Trans. Circuit Theory 18, 507-519 (1971).", "year": 1971}
    parse_citation_heuristic(cit)
    assert cit.get("pages") == "507--519"


# ---- Does not overwrite existing fields ----

def test_no_overwrite():
    cit = {
        "raw_text": "Chua, L. O. Memristor. IEEE Trans. 18, 507 (1971).",
        "title": "Already Set",
        "authors": ["Existing Author"],
    }
    parse_citation_heuristic(cit)
    assert cit["title"] == "Already Set"
    assert cit["authors"] == ["Existing Author"]


# ---- Short/garbage input ----

def test_short_input():
    cit = {"raw_text": "too short"}
    parse_citation_heuristic(cit)
    assert not cit.get("title")


# ---- Fingerprint ----

def test_fingerprint_doi():
    assert _citation_fingerprint({"doi": "10.1234/test"}) == "doi:10.1234/test"


def test_fingerprint_author_year():
    fp = _citation_fingerprint({"author_last_names": ["Smith", "Jones"], "year": 2020})
    assert "smith" in fp
    assert "2020" in fp


def test_fingerprint_empty():
    assert _citation_fingerprint({}) == ""


# ---- Cross-paper fusion ----

def test_fusion_fills_missing_fields():
    cits_a = [
        {"raw_text": "Smith 2020", "doi": "10.1234/x", "title": "Full Title Here", "year": 2020, "author_last_names": []},
    ]
    cits_b = [
        {"raw_text": "Smith 2020 short", "doi": "10.1234/x", "year": 2020, "venue": "Nature", "author_last_names": []},
    ]
    fuse_cross_paper_evidence([cits_a, cits_b])
    # cits_b should now have the title from cits_a
    assert cits_b[0].get("title") == "Full Title Here"
    # cits_a should now have the venue from cits_b
    assert cits_a[0].get("venue") == "Nature"
