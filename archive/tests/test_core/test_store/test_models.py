"""Tests for wikify.core.store.models — Paper.parsed_authors and display_name."""

from __future__ import annotations

import json

from wikify.core.store.models import Paper


def _make_paper(**kwargs) -> Paper:
    defaults = {
        "id": "abc123",
        "title": "Test Paper",
        "authors": "[]",
        "year": 2021,
    }
    defaults.update(kwargs)
    return Paper(**defaults)


# ── parsed_authors ────────────────────────────────────────────────────────────


def test_parsed_authors_empty_string():
    p = _make_paper(authors="")
    assert p.parsed_authors == []


def test_parsed_authors_valid_json_list():
    names = ["Alice Brown", "Bob Green"]
    p = _make_paper(authors=json.dumps(names))
    assert p.parsed_authors == names


def test_parsed_authors_single_author():
    p = _make_paper(authors=json.dumps(["Carol White"]))
    assert p.parsed_authors == ["Carol White"]


def test_parsed_authors_malformed_json():
    p = _make_paper(authors="not valid json {{{")
    assert p.parsed_authors == []


def test_parsed_authors_json_null():
    # json.loads("null") returns Python None; the code returns it as-is
    # (TypeError is not raised until the caller iterates — the property just returns None)
    p = _make_paper(authors="null")
    result = p.parsed_authors
    # The implementation returns None when JSON decodes to a non-list scalar without raising
    assert result is None or result == []


def test_parsed_authors_empty_json_list():
    p = _make_paper(authors="[]")
    assert p.parsed_authors == []


def test_parsed_authors_unicode_names():
    names = ["José García", "Müller Hans"]
    p = _make_paper(authors=json.dumps(names))
    assert p.parsed_authors == names


# ── display_name ──────────────────────────────────────────────────────────────


def test_display_name_basic():
    p = _make_paper(
        authors=json.dumps(["Kim Jae-Won", "Lee Sung-Ho"]),
        year=2021,
        title="4K-Memristor Array",
    )
    name = p.display_name()
    assert name.startswith("Jae-Won")
    assert "2021" in name
    assert "4K-Memristor Array" in name


def test_display_name_uses_last_word_of_first_author():
    # "Alice Brown" → last word is "Brown"
    p = _make_paper(
        authors=json.dumps(["Alice Brown"]),
        year=2020,
        title="Some Title",
    )
    name = p.display_name()
    assert name.startswith("Brown")


def test_display_name_no_authors():
    p = _make_paper(authors="[]", year=2019, title="Orphan Paper")
    name = p.display_name()
    assert name.startswith("Unknown")
    assert "2019" in name


def test_display_name_no_year():
    p = _make_paper(authors=json.dumps(["Smith John"]), year=None, title="No Year Paper")
    name = p.display_name()
    assert "YYYY" in name


def test_display_name_sanitizes_forbidden_chars():
    p = _make_paper(
        authors=json.dumps(["Smith John"]),
        year=2020,
        title='Title: With "Quotes" and <Brackets>',
    )
    name = p.display_name()
    for ch in '<>:"/\\|?*':
        assert ch not in name


def test_display_name_truncated_to_200():
    long_title = "A" * 300
    p = _make_paper(
        authors=json.dumps(["Doe Jane"]),
        year=2021,
        title=long_title,
    )
    name = p.display_name()
    assert len(name) <= 200


def test_display_name_format():
    p = _make_paper(
        authors=json.dumps(["Lee Kang"]),
        year=2023,
        title="Thin Film Growth",
    )
    name = p.display_name()
    assert " - " in name
    parts = name.split(" - ", 1)
    assert "Lee" in parts[0] or "Kang" in parts[0]
    assert "Thin Film Growth" in parts[1]
