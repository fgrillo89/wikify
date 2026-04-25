"""Tests for BibTeX formatting."""

from __future__ import annotations

from wikify.citations.bibtex import openalex_to_bibtex


def test_basic_formatting(sample_work_json):
    bib = openalex_to_bibtex(sample_work_json)
    assert bib.startswith("@article{Harris2020Array,")
    assert "title = {{Array programming with NumPy}}" in bib
    assert "author = {Charles R. Harris and K. Jarrod Millman}" in bib
    assert "year = {2020}" in bib
    assert "journal = {Nature}" in bib
    assert "volume = {585}" in bib
    assert "pages = {357--362}" in bib
    assert "doi = {10.1038/s41586-020-2649-2}" in bib


def test_missing_fields():
    work = {
        "id": "https://openalex.org/W1",
        "doi": None,
        "title": "Minimal Work",
        "publication_year": None,
        "authorships": [],
        "biblio": {},
        "primary_location": None,
        "referenced_works": [],
        "type": "journal-article",
    }
    bib = openalex_to_bibtex(work)
    assert "@article{" in bib
    assert "title = {{Minimal Work}}" in bib
    # No author, year, journal lines since they're empty
    assert "author = {}" not in bib
    assert "year = {}" not in bib


def test_special_characters_escaped():
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1234/test",
        "title": "TiO2 & ZnO: 100% efficiency",
        "publication_year": 2023,
        "authorships": [
            {"author": {"display_name": "M. O'Brien"}, "author_position": "first"},
        ],
        "biblio": {},
        "primary_location": None,
        "referenced_works": [],
        "type": "journal-article",
    }
    bib = openalex_to_bibtex(work)
    assert r"TiO2 \& ZnO: 100\% efficiency" in bib
