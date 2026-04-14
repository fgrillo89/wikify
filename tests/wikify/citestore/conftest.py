"""Shared fixtures for citestore tests."""

from __future__ import annotations

import pytest

SAMPLE_OPENALEX_WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1038/s41586-020-2649-2",
    "title": "Array programming with NumPy",
    "publication_year": 2020,
    "type": "journal-article",
    "cited_by_count": 8500,
    "authorships": [
        {
            "author_position": "first",
            "author": {
                "id": "https://openalex.org/A1",
                "display_name": "Charles R. Harris",
                "orcid": None,
            },
            "institutions": [],
        },
        {
            "author_position": "middle",
            "author": {
                "id": "https://openalex.org/A2",
                "display_name": "K. Jarrod Millman",
                "orcid": None,
            },
            "institutions": [],
        },
    ],
    "biblio": {
        "volume": "585",
        "issue": "7825",
        "first_page": "357",
        "last_page": "362",
    },
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S123",
            "display_name": "Nature",
            "issn_l": "0028-0836",
            "host_organization_name": "Springer Nature",
        },
    },
    "referenced_works": [
        "https://openalex.org/W100",
        "https://openalex.org/W200",
        "https://openalex.org/W300",
    ],
}

SAMPLE_OPENALEX_CHILD = {
    "id": "https://openalex.org/W100",
    "doi": "https://doi.org/10.1000/child-work",
    "title": "A Child Reference Paper",
    "publication_year": 2015,
    "type": "journal-article",
    "cited_by_count": 100,
    "authorships": [
        {
            "author_position": "first",
            "author": {
                "id": "https://openalex.org/A10",
                "display_name": "Jane Doe",
                "orcid": None,
            },
            "institutions": [],
        },
    ],
    "biblio": {"volume": "10", "issue": "1", "first_page": "1", "last_page": "10"},
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S456",
            "display_name": "Journal of Testing",
            "issn_l": "1234-5678",
            "host_organization_name": "Test Publisher",
        },
    },
    "referenced_works": [],
}


@pytest.fixture
def sample_work_json():
    return SAMPLE_OPENALEX_WORK.copy()


@pytest.fixture
def sample_child_json():
    return SAMPLE_OPENALEX_CHILD.copy()
