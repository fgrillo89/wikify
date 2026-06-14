"""Handle resolution for committed wiki pages.

Wiki filenames have used two conventions over time: current bundles keep
the title's spaces (``Atomic Layer Deposition.md``) while older bundles
used kebab-case (``atomic-layer-deposition.md``). A handle must resolve
the page regardless of which convention produced the file, so an agent
(and the wikify-search-wiki skill examples) can refer to a concept by its
natural title.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Bundle
from wikify.bundle.wiki.queries import AmbiguousSlugError, resolve_slug, show_page


def _write_page(bundle: Bundle, *, filename: str, title: str, kind: str = "article") -> None:
    sub = bundle.wiki_articles_dir if kind == "article" else bundle.wiki_people_dir
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{filename}.md").write_text(
        f"---\nid: {title}\nkind: {kind}\ntitle: {title}\n---\n\n# {title}\n\nBody.\n",
        encoding="utf-8",
    )


def _bundle(tmp_path: Path) -> Bundle:
    return Bundle(root=tmp_path / "bundle")


def test_kebab_file_resolves_by_natural_title(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    assert resolve_slug(b, "Atomic Layer Deposition") == (
        "atomic-layer-deposition",
        "article",
    )
    assert resolve_slug(b, "atomic layer deposition") == (
        "atomic-layer-deposition",
        "article",
    )


def test_spaced_file_resolves_by_kebab_handle(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="Atomic Layer Deposition", title="Atomic Layer Deposition")
    assert resolve_slug(b, "atomic-layer-deposition") == (
        "Atomic Layer Deposition",
        "article",
    )


def test_exact_filename_still_wins(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    assert resolve_slug(b, "atomic-layer-deposition") == (
        "atomic-layer-deposition",
        "article",
    )


def test_prefix_match_preserved(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    assert resolve_slug(b, "atomic") == ("atomic-layer-deposition", "article")


def test_normalized_match_disambiguates_prefix(tmp_path: Path) -> None:
    # Both pages share the "memristor" prefix; the lowercase handle is an
    # exact normalized match for one of them, so it resolves rather than
    # raising the prefix ambiguity it would have before.
    b = _bundle(tmp_path)
    _write_page(b, filename="Memristor", title="Memristor")
    _write_page(b, filename="Memristor Crossbar Array", title="Memristor Crossbar Array")
    assert resolve_slug(b, "memristor") == ("Memristor", "article")


def test_ambiguous_normalized_match_raises(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    _write_page(b, filename="Atomic Layer Deposition", title="Atomic Layer Deposition")
    with pytest.raises(AmbiguousSlugError):
        resolve_slug(b, "atomic layer deposition")


def test_unknown_handle_returns_none(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    assert resolve_slug(b, "nonexistent concept") is None


def test_show_page_surfaces_title_for_kebab_file(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    _write_page(b, filename="atomic-layer-deposition", title="Atomic Layer Deposition")
    info = show_page(b, handle="Atomic Layer Deposition")
    assert info is not None
    assert info["slug"] == "atomic-layer-deposition"
    assert info["title"] == "Atomic Layer Deposition"
