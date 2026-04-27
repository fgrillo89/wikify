"""Tests for store.page_naming: natural Wikipedia-style page ids."""

from wikify.bundle.wiki.page_naming import (
    page_filename,
    page_id_from_title,
    url_slug,
)


def test_page_id_from_title_basic():
    assert page_id_from_title("Atomic Layer Deposition") == "Atomic Layer Deposition"
    assert page_id_from_title("  Leon  Chua ") == "Leon Chua"
    assert page_id_from_title("TiO2") == "TiO2"


def test_page_id_reserved_chars_sanitized():
    assert "/" not in page_id_from_title("A/B")
    assert ":" not in page_id_from_title("foo:bar")
    assert "?" not in page_id_from_title("why?")
    # Reserved device name.
    assert page_id_from_title("nul").endswith("_")


def test_page_filename_has_md_extension():
    assert page_filename("Atomic Layer Deposition") == "Atomic Layer Deposition.md"


def test_url_slug_uses_underscores():
    assert url_slug("Atomic Layer Deposition") == "Atomic_Layer_Deposition"
    assert url_slug("Leon Chua") == "Leon_Chua"


def test_round_trip_title_to_filename_to_slug():
    title = "Atomic Layer Deposition"
    pid = page_id_from_title(title)
    fn = page_filename(pid)
    slug = url_slug(pid)
    assert fn == "Atomic Layer Deposition.md"
    assert slug == "Atomic_Layer_Deposition"




