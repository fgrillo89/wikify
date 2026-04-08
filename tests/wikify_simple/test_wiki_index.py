"""Tests for store.wiki_index: alias, reverse lookup, atomic save, rebuild, md render."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wikify_simple.models import Evidence, WikiPage
from wikify_simple.paths import BundlePaths
from wikify_simple.store.wiki_files import write_page
from wikify_simple.store.wiki_index import (
    WikiIndex,
    build_index,
)


def _make_page(pid: str, title: str, aliases: list[str], doc_id: str, links=None):
    return WikiPage(
        id=pid,
        kind="concept",
        title=title,
        aliases=aliases,
        body_markdown=f"# {title}\n\n{title} description.[^e1]",
        evidence=[Evidence(marker="e1", chunk_id=f"{doc_id}_c1", doc_id=doc_id, quote="desc")],
        links=list(links or []),
    )


@pytest.fixture
def bundle(tmp_path) -> BundlePaths:
    b = BundlePaths(root=tmp_path / "bundle")
    b.ensure()
    pages = [
        _make_page(
            "concept-photocatalysis",
            "Photocatalysis",
            ["photo-catalysis"],
            "doc1",
            links=["concept-tio2"],
        ),
        _make_page("concept-tio2", "TiO2", ["titanium dioxide"], "doc1"),
    ]
    for p in pages:
        write_page(b, p)
    build_index(b, pages).save()
    return b


def test_resolve_alias_title_and_alias(bundle):
    idx = WikiIndex.load(bundle)
    assert idx.resolve_alias("Photocatalysis") == "concept-photocatalysis"
    assert idx.resolve_alias("photo-catalysis") == "concept-photocatalysis"
    assert idx.resolve_alias("TiO2") == "concept-tio2"
    assert idx.resolve_alias("titanium dioxide") == "concept-tio2"
    assert idx.resolve_alias("nonexistent") is None


def test_pages_for_doc_reverse_lookup(bundle):
    idx = WikiIndex.load(bundle)
    pids = sorted(idx.pages_for_doc("doc1"))
    assert pids == ["concept-photocatalysis", "concept-tio2"]


def test_atomic_save_keeps_existing_file(bundle):
    idx = WikiIndex.load(bundle)
    original = (bundle.root / "_index.json").read_bytes()

    with patch("wikify_simple.store.wiki_index.os.replace", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            idx.save()
    # original file must still be intact and loadable
    current = (bundle.root / "_index.json").read_bytes()
    assert current == original
    reloaded = WikiIndex.load(bundle)
    assert "concept-photocatalysis" in reloaded


def test_rebuild_path_when_missing(bundle):
    idx_path = bundle.root / "_index.json"
    idx_path.unlink()
    idx = WikiIndex.load(bundle)
    assert "concept-photocatalysis" in idx
    assert "concept-tio2" in idx
    assert idx.resolve_alias("TiO2") == "concept-tio2"


def test_index_md_contains_bullets(bundle):
    md_path = bundle.root / "_index.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "# Wiki index" in text
    assert "Photocatalysis" in text
    # bullet with path; path must resolve under bundle.root
    for line in text.splitlines():
        if line.startswith("- ["):
            # extract path in parens
            start = line.find("](") + 2
            end = line.find(")", start)
            rel = line[start:end]
            assert (bundle.root / rel).exists(), f"link does not resolve: {rel}"
