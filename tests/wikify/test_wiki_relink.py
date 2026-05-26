"""Tests for ``wikify wiki relink`` — shared-evidence link refresh."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.bundle.wiki.page import load_bundle, parse_page
from wikify.bundle.wiki.relink import apply_relinks, compute_relinks
from wikify.cli import app

runner = CliRunner()


def _write_page(
    wiki_dir: Path, page_id: str, doc_ids: list[str], links: list[str] | None = None
) -> Path:
    """Write a minimal article page with an evidence block."""
    articles = wiki_dir / "articles"
    articles.mkdir(parents=True, exist_ok=True)
    slug = page_id.replace(" ", "_").lower()
    path = articles / f"{slug}.md"
    links_line = f"links: {json.dumps(links or [])}"
    body = [
        "---",
        f"id: {page_id}",
        "kind: article",
        f"title: {page_id}",
        "aliases: []",
        links_line,
        "---",
        "",
        f"# {page_id}",
        "",
        f"This is the body of {page_id}, which has enough text to pass the prose check. " * 4,
        "",
        "## Evidence",
        "",
    ]
    for i, doc in enumerate(doc_ids, 1):
        body.append(
            f'[^e{i}]: chunk_id_{doc} ({doc}) > "Quote from {doc}."'
        )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _bundle_root(tmp_path: Path) -> Path:
    """Create a minimal bundle dir that the CLI resolver will accept."""
    from wikify.api import Bundle
    from wikify.bundle.run.lifecycle import init_run

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path="data/corpora/foo")
    (bundle.wiki_dir / "articles").mkdir(parents=True, exist_ok=True)
    return bundle_dir


def test_compute_relinks_picks_top_overlap(tmp_path: Path) -> None:
    """Page A shares 3 docs with B, 2 with C, 1 with D. Top-2 -> [B, C]."""
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2", "d3", "d4"])
    _write_page(wiki, "B", ["d1", "d2", "d3"])
    _write_page(wiki, "C", ["d3", "d4"])
    _write_page(wiki, "D", ["d4"])
    bundle = load_bundle(wiki)

    result = compute_relinks(bundle, max_links=2, min_overlap=1)
    assert result["A"] == ["B", "C"]  # B has overlap 3, C has overlap 2


def test_compute_relinks_respects_min_overlap(tmp_path: Path) -> None:
    """A peer needs at least ``min_overlap`` shared docs to be linked."""
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2"])
    _write_page(wiki, "B", ["d1"])  # overlap 1, below threshold
    _write_page(wiki, "C", ["d1", "d2"])  # overlap 2, meets threshold
    bundle = load_bundle(wiki)

    result = compute_relinks(bundle, max_links=5, min_overlap=2)
    assert result["A"] == ["C"]
    assert result["B"] == []  # B's overlap with each other page is < 2


def test_apply_relinks_writes_frontmatter(tmp_path: Path) -> None:
    """Page that gained a peer after incremental add gets its links updated."""
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    # Original two pages share 2 docs, but neither knows the other yet.
    _write_page(wiki, "Memristor", ["d1", "d2"], links=[])
    _write_page(wiki, "Hafnium Oxide", ["d1", "d2"], links=[])
    bundle = load_bundle(wiki)

    result = apply_relinks(bundle, max_links=5, min_overlap=2)
    assert set(result.updated) == {"Memristor", "Hafnium Oxide"}

    # Re-parse to verify frontmatter was rewritten.
    memristor = parse_page(wiki / "articles" / "memristor.md")
    assert memristor.links == ["Hafnium Oxide"]


def test_apply_relinks_dry_run_does_not_write(tmp_path: Path) -> None:
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2"], links=[])
    _write_page(wiki, "B", ["d1", "d2"], links=[])
    bundle = load_bundle(wiki)

    result = apply_relinks(bundle, max_links=5, min_overlap=2, dry_run=True)
    assert len(result.updated) == 2  # would-update count

    # Files unchanged.
    a_after = parse_page(wiki / "articles" / "a.md")
    assert a_after.links == []


def test_apply_relinks_unchanged_when_already_correct(tmp_path: Path) -> None:
    """A page whose existing ``links`` already matches the computed set is skipped."""
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2"], links=["B"])
    _write_page(wiki, "B", ["d1", "d2"], links=["A"])
    bundle = load_bundle(wiki)

    result = apply_relinks(bundle, max_links=5, min_overlap=2)
    assert sorted(result.unchanged) == ["A", "B"]
    assert result.updated == []


def test_cli_relink_text_output(tmp_path: Path) -> None:
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2"], links=[])
    _write_page(wiki, "B", ["d1", "d2"], links=[])

    result = runner.invoke(app, ["wiki", "relink", "--run", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert "updated 2 page(s)" in result.output
    # Verify file was actually rewritten.
    a = parse_page(wiki / "articles" / "a.md")
    assert a.links == ["B"]


def test_cli_relink_json_envelope(tmp_path: Path) -> None:
    bundle_root = _bundle_root(tmp_path)
    wiki = bundle_root / "wiki"
    assert wiki.exists(), "bundle wiki dir must exist for the test setup"
    _write_page(wiki, "A", ["d1", "d2"], links=[])
    _write_page(wiki, "B", ["d1", "d2"], links=[])

    result = runner.invoke(
        app, ["wiki", "relink", "--run", str(bundle_root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["updated"] == 2
    assert payload["unchanged"] == 0
