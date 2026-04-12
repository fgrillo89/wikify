"""Tests for store.wiki_index: alias, reverse lookup, atomic save, rebuild, md render."""

from unittest.mock import patch

import pytest

from wikify.models import Evidence, WikiPage
from wikify.paths import BundlePaths
from wikify.store.wiki_files import write_page
from wikify.store.wiki_index import (
    WikiIndex,
    build_index,
    migrate_concepts_dir,
    rebuild_index,
)


def _make_page(pid: str, title: str, aliases: list[str], doc_id: str, links=None):
    # body_markdown must be >= 200 chars so build_index does not treat it as a skeleton
    body = (
        f"# {title}\n\n"
        f"{title} is a concept studied in materials science. "
        f"It involves specific chemical and physical processes that have been documented "
        f"extensively in the scientific literature.[^e1]\n\n"
        f"## References\n\n"
        f"[^e1]: {doc_id}_c1 ({doc_id}) > \"{title} description\""
    )
    return WikiPage(
        id=pid,
        kind="article",
        title=title,
        aliases=aliases,
        body_markdown=body,
        evidence=[Evidence(marker="e1", chunk_id=f"{doc_id}_c1", doc_id=doc_id, quote="desc")],
        links=list(links or []),
    )


@pytest.fixture
def bundle(tmp_path) -> BundlePaths:
    b = BundlePaths(root=tmp_path / "bundle")
    b.ensure()
    pages = [
        _make_page(
            "Photocatalysis",
            "Photocatalysis",
            ["photo-catalysis"],
            "doc1",
            links=["TiO2"],
        ),
        _make_page("TiO2", "TiO2", ["titanium dioxide"], "doc1"),
    ]
    for p in pages:
        write_page(b, p)
    build_index(b, pages).save()
    return b


def test_resolve_alias_title_and_alias(bundle):
    idx = WikiIndex.load(bundle)
    assert idx.resolve_alias("Photocatalysis") == "Photocatalysis"
    assert idx.resolve_alias("photo-catalysis") == "Photocatalysis"
    assert idx.resolve_alias("TiO2") == "TiO2"
    assert idx.resolve_alias("titanium dioxide") == "TiO2"
    assert idx.resolve_alias("nonexistent") is None


def test_pages_for_doc_reverse_lookup(bundle):
    idx = WikiIndex.load(bundle)
    pids = sorted(idx.pages_for_doc("doc1"))
    assert pids == ["Photocatalysis", "TiO2"]


def test_atomic_save_keeps_existing_file(bundle):
    idx = WikiIndex.load(bundle)
    original = (bundle.root / "_index.json").read_bytes()

    with patch("wikify.store.wiki_index.os.replace", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            idx.save()
    # original file must still be intact and loadable
    current = (bundle.root / "_index.json").read_bytes()
    assert current == original
    reloaded = WikiIndex.load(bundle)
    assert "Photocatalysis" in reloaded


def test_load_returns_empty_when_missing(bundle):
    idx_path = bundle.root / "_index.json"
    idx_path.unlink()
    idx = WikiIndex.load(bundle)
    assert len(idx) == 0, "load() must not rebuild implicitly"


def test_rebuild_index_when_missing(bundle):
    idx_path = bundle.root / "_index.json"
    idx_path.unlink()
    idx = rebuild_index(bundle)
    assert "Photocatalysis" in idx
    assert "TiO2" in idx
    assert idx.resolve_alias("TiO2") == "TiO2"


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


def test_migrate_concepts_dir_roundtrip(tmp_path):
    """An old bundle with a concepts/ directory is migrated via explicit
    migrate_concepts_dir() call. Idempotent: a second call is a no-op.
    """
    root = tmp_path / "bundle"
    old_dir = root / "concepts"
    old_dir.mkdir(parents=True)
    page_text = (
        "---\n"
        "id: Photocatalysis\n"
        "kind: concept\n"
        "title: Photocatalysis\n"
        "aliases: []\n"
        "links: []\n"
        "---\n"
        "\n# Photocatalysis\n\nBody.[^e1]\n\n"
        "## Evidence\n\n"
        '[^e1]: c1 (doc1) > "q"\n'
    )
    (old_dir / "Photocatalysis.md").write_text(page_text, encoding="utf-8")

    bundle = BundlePaths(root=root)
    # Explicit migration.
    assert migrate_concepts_dir(bundle) is True

    assert not (root / "concepts").exists(), "concepts/ should have been renamed"
    assert (root / "articles").exists(), "articles/ should now exist"

    # Frontmatter kind rewritten to "article".
    migrated_text = (root / "articles" / "Photocatalysis.md").read_text(encoding="utf-8")
    assert "kind: article" in migrated_text

    # Rebuild index after migration.
    idx = rebuild_index(bundle)
    assert "Photocatalysis" in idx

    # Second call: idempotent.
    assert migrate_concepts_dir(bundle) is False
    assert not (root / "concepts").exists()


def test_build_index_excludes_skeleton_pages(tmp_path):
    """build_index must not enumerate pages with body_markdown shorter than 200 chars."""
    b = BundlePaths(root=tmp_path / "bundle")
    b.ensure()
    full_body = (
        "# Real Page\n\n"
        "Real Page is a well-documented concept in materials science with substantial "
        "content backed by multiple primary sources in the scientific literature.[^e1]\n\n"
        "## References\n\n"
        "[^e1]: doc1_c1 (doc1) > \"real content from the primary literature\""
    )
    assert len(full_body) >= 200, "test setup error: full_body must be >= 200 chars"
    skeleton_body = "# Stub\n\nStub.[^e1]"  # <200 chars

    pages = [
        WikiPage(
            id="Real Page",
            kind="article",
            title="Real Page",
            aliases=[],
            body_markdown=full_body,
            evidence=[Evidence(marker="e1", chunk_id="doc1_c1", doc_id="doc1", quote="real")],
            links=[],
        ),
        WikiPage(
            id="Another Page",
            kind="article",
            title="Another Page",
            aliases=[],
            body_markdown=full_body.replace("Real Page", "Another Page"),
            evidence=[Evidence(marker="e1", chunk_id="doc2_c1", doc_id="doc2", quote="another")],
            links=[],
        ),
        WikiPage(
            id="Stub",
            kind="article",
            title="Stub",
            aliases=[],
            body_markdown=skeleton_body,
            evidence=[Evidence(marker="e1", chunk_id="doc3_c1", doc_id="doc3", quote="stub")],
            links=[],
        ),
    ]
    for p in pages:
        write_page(b, p)

    idx = build_index(b, pages)
    assert len(idx) == 2, f"expected 2 real pages, got {len(idx)}"
    assert "Real Page" in idx
    assert "Another Page" in idx
    assert "Stub" not in idx
