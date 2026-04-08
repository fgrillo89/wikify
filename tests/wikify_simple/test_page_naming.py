"""Tests for store.page_naming: natural Wikipedia-style page ids."""

from __future__ import annotations

from wikify_simple.models import Evidence, WikiPage
from wikify_simple.paths import BundlePaths
from wikify_simple.store.page_naming import (
    page_filename,
    page_id_from_title,
    url_slug,
)
from wikify_simple.store.wiki_files import write_page
from wikify_simple.store.wiki_index import (
    WikiIndex,
    build_index,
    migrate_legacy_page_ids,
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


def test_alias_resolution_case_insensitive(tmp_path):
    b = BundlePaths(root=tmp_path / "bundle")
    b.ensure()
    page = WikiPage(
        id="Atomic Layer Deposition",
        kind="concept",
        title="Atomic Layer Deposition",
        aliases=["ALD"],
        body_markdown="# Atomic Layer Deposition\n\nBody.[^e1]",
        evidence=[Evidence(marker="e1", chunk_id="c1", doc_id="d1", quote="q")],
    )
    write_page(b, page)
    build_index(b, [page]).save()
    idx = WikiIndex.load(b)
    assert idx.resolve_alias("ald") == "Atomic Layer Deposition"
    assert idx.resolve_alias("Atomic Layer Deposition") == "Atomic Layer Deposition"


def test_migrate_legacy_page_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKIFY_SKIP_LEGACY_MIGRATION", "1")
    b = BundlePaths(root=tmp_path / "bundle")
    b.ensure()
    legacy = b.concepts_dir / "concept-atomic-layer-deposition.md"
    legacy.write_text(
        "---\n"
        "id: concept-atomic-layer-deposition\n"
        "kind: concept\n"
        "title: Atomic Layer Deposition\n"
        "aliases: []\n"
        "links: []\n"
        "---\n"
        "\n# Atomic Layer Deposition\n\nBody.[^e1]\n\n## Evidence\n\n"
        '[^e1]: c1 (d1) > "q"\n',
        encoding="utf-8",
    )
    n = migrate_legacy_page_ids(b)
    assert n == 1
    assert not legacy.exists()
    assert (b.concepts_dir / "Atomic Layer Deposition.md").exists()
