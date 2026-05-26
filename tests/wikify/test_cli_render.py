"""Tests for `wikify render` — static-site renderer over a bundle."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_wiki_commit import _setup_validated  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def _commit_one_article(tmp_path: Path):
    bundle, slug = _setup_validated(tmp_path)
    runner.invoke(app, ["wiki", "commit", slug, "--run", str(bundle.root)])
    return bundle, slug


def test_render_writes_html_site(tmp_path: Path) -> None:
    bundle, slug = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--format",
            "html",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert (out / "articles").is_dir()
    # The rendered article file should mention the page title.
    html_files = list((out / "articles").glob("*.html"))
    assert html_files, "expected at least one rendered article HTML"
    text = html_files[0].read_text(encoding="utf-8")
    assert "Atomic Layer Deposition" in text


def test_render_default_out_under_derived(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(app, ["render", "--bundle", str(bundle.root)])
    assert result.exit_code == 0, result.output
    assert (bundle.derived_dir / "site" / "index.html").is_file()


def test_render_search_index_uses_plain_text_excerpt(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    page_path = bundle.wiki_articles_dir / "atomic-layer-deposition.md"
    text = page_path.read_text(encoding="utf-8")
    page_path.write_text(
        text.replace(
            "Atomic Layer Deposition is",
            "**Atomic Layer Deposition** is",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["render", "--bundle", str(bundle.root)])

    assert result.exit_code == 0, result.output
    search_index = json.loads(
        (bundle.derived_dir / "site" / "search-index.json").read_text(
            encoding="utf-8"
        )
    )
    assert "**" not in search_index[0]["excerpt"]
    assert "[^e1]" not in search_index[0]["excerpt"]


def test_render_rejects_unsupported_format(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--format", "pdf"]
    )
    assert result.exit_code != 0


def test_wiki_name_derives_from_corpus_basename(tmp_path: Path) -> None:
    from wikify.render.html.render import derive_wiki_name

    assert derive_wiki_name(Path("data/corpora/ald_docling_2026_05_15")) == "ALD Wiki"
    assert derive_wiki_name(Path("/tmp/cvd_marker_rechunked")) == "CVD Wiki"
    assert derive_wiki_name(None) == "ScholarForge"


def test_render_wiki_name_override(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--out",
            str(out),
            "--wiki-name",
            "My Custom Wiki",
        ],
    )
    assert result.exit_code == 0, result.output
    index_html = (out / "index.html").read_text(encoding="utf-8")
    assert "My Custom Wiki" in index_html
    assert "ScholarForge" not in index_html


def test_render_emits_references_page(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    refs_html = (out / "references.html").read_text(encoding="utf-8")
    assert "<h1>References</h1>" in refs_html
    assert "Cited in:" in refs_html
    assert "Atomic Layer Deposition" in refs_html


def test_render_emits_topics_graph_when_navigation_present(tmp_path: Path) -> None:
    """graph.html exists only when a navigation.json file is present."""
    bundle, _ = _commit_one_article(tmp_path)
    nav_path = bundle.derived_dir / "navigation.json"
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    nav_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy": "test",
                "groups": [
                    {
                        "id": "thin-films",
                        "title": "Thin films",
                        "description": "",
                        "page_ids": ["Atomic Layer Deposition"],
                        "children": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    graph_html = (out / "graph.html").read_text(encoding="utf-8")
    assert "Topics graph" in graph_html
    assert "d3@7" in graph_html
    # Page node id should be embedded in the JSON-encoded graph data.
    assert "page:Atomic Layer Deposition" in graph_html


def test_render_skips_topics_graph_without_navigation(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert not (out / "graph.html").exists()


def test_render_json_envelope(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--out",
            str(out),
            "--output-format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["pages"] >= 1
    assert payload["out"].endswith("site")


def test_navigation_context_apply_and_render_grouped_front_page(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    context_path = tmp_path / "navigation_context.json"
    result = runner.invoke(
        app,
        [
            "wiki",
            "navigation-context",
            "--run",
            str(bundle.root),
            "--out",
            str(context_path),
        ],
    )
    assert result.exit_code == 0, result.output
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["pages"][0]["id"] == "Atomic Layer Deposition"

    nav_path = tmp_path / "navigation.json"
    nav_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy": "test",
                "groups": [
                    {
                        "id": "thin-films",
                        "title": "Thin films",
                        "description": "Thin-film methods and uses.",
                        "page_ids": ["Atomic Layer Deposition"],
                        "children": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["wiki", "apply-navigation", str(nav_path), "--run", str(bundle.root)],
    )
    assert result.exit_code == 0, result.output
    import sqlite3

    con = sqlite3.connect(bundle.sqlite_path)
    try:
        category_count = con.execute("SELECT COUNT(*) FROM wiki_categories").fetchone()[0]
        membership_count = con.execute(
            "SELECT COUNT(*) FROM wiki_category_pages"
        ).fetchone()[0]
    finally:
        con.close()
    assert category_count == 1
    assert membership_count == 1

    out = tmp_path / "site"
    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--corpus",
            str(tmp_path / "corpus"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Browse by topic" in html
    assert "Thin films" in html
    assert "source articles used" in html
    assert "chunks" not in html.lower()


def _write_committed_article(
    bundle,
    *,
    slug: str,
    page_id: str,
    title: str,
    links: list[str] | None = None,
    doc_id: str = "paper_0",
    body_term: str = "deposition",
) -> Path:
    body = (
        "---\n"
        f"id: {json.dumps(page_id)}\n"
        "kind: article\n"
        f"title: {json.dumps(title)}\n"
        "aliases: []\n"
        f"links: {json.dumps(links or [])}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{title} is a {body_term} topic connected to thin-film processing and "
        "surface reactions in the same evidence base.\n\n"
        "## References\n\n"
        f'[^e1]: {doc_id}__c0001 ({doc_id}) > "shared evidence quote"\n'
    )
    path = bundle.wiki_articles_dir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_navigation_context_includes_cluster_hints(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    _write_committed_article(
        bundle,
        slug="chemical-vapor-deposition",
        page_id="Chemical Vapor Deposition",
        title="Chemical Vapor Deposition",
        links=["Atomic Layer Deposition"],
        doc_id="paper_0",
    )
    _write_committed_article(
        bundle,
        slug="surface-chemistry",
        page_id="Surface Chemistry",
        title="Surface Chemistry",
        links=[],
        doc_id="paper_9",
        body_term="surface chemistry",
    )

    context_path = tmp_path / "navigation_context.json"
    result = runner.invoke(
        app,
        [
            "wiki",
            "navigation-context",
            "--run",
            str(bundle.root),
            "--out",
            str(context_path),
        ],
    )

    assert result.exit_code == 0, result.output
    context = json.loads(context_path.read_text(encoding="utf-8"))
    hints = {item["page_id"]: item["related"] for item in context["cluster_hints"]}
    atomic_related = hints["Atomic Layer Deposition"]
    cvd_hint = next(
        item for item in atomic_related if item["page_id"] == "Chemical Vapor Deposition"
    )
    assert cvd_hint["score"] > 0
    assert cvd_hint["reasons"]["linked_by"] is True
    assert cvd_hint["reasons"]["shared_evidence_doc_ids"] == ["paper_0"]
    assert "deposition" in cvd_hint["reasons"]["overlap_terms"]


def test_navigation_context_includes_existing_navigation_and_freshness(
    tmp_path: Path,
) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    nav_path = tmp_path / "navigation.json"
    nav_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy": "test",
                "groups": [
                    {
                        "id": "thin-films",
                        "title": "Thin films",
                        "description": "Thin-film methods.",
                        "page_ids": ["Atomic Layer Deposition"],
                        "children": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["wiki", "apply-navigation", str(nav_path), "--run", str(bundle.root)],
    )
    assert result.exit_code == 0, result.output

    _write_committed_article(
        bundle,
        slug="chemical-vapor-deposition",
        page_id="Chemical Vapor Deposition",
        title="Chemical Vapor Deposition",
        links=["Atomic Layer Deposition"],
        doc_id="paper_0",
    )

    context_path = tmp_path / "navigation_context.json"
    result = runner.invoke(
        app,
        [
            "wiki",
            "navigation-context",
            "--run",
            str(bundle.root),
            "--out",
            str(context_path),
        ],
    )

    assert result.exit_code == 0, result.output
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["existing_navigation"]["groups"][0]["id"] == "thin-films"
    assert "page_fingerprints" not in context["existing_navigation"]
    assert context["freshness"]["has_navigation"] is True
    assert context["freshness"]["is_fresh"] is False
    assert context["freshness"]["new_page_ids"] == ["Chemical Vapor Deposition"]


def test_render_selected_figure_placeholder(tmp_path: Path) -> None:
    bundle, _ = _commit_one_article(tmp_path)
    corpus = tmp_path / "corpus"
    image = corpus / "images" / "paper_0" / "Figure_01.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_path = bundle.wiki_articles_dir / "atomic-layer-deposition.md"
    text = page_path.read_text(encoding="utf-8")
    page_path.write_text(
        text.replace(
            "## Applications",
            "Figure 1 summarizes the cycle.\n\n{{figure:fig1}}\n\n## Applications",
        ),
        encoding="utf-8",
    )
    page_path.with_suffix(".figures.json").write_text(
        json.dumps(
            [
                {
                    "figure_id": "paper_0/Figure_01",
                    "path": "images/paper_0/Figure_01.png",
                    "caption": "Schematic overview of an ALD cycle.",
                    "placement_anchor": "fig1",
                    "source_marker": "e1",
                }
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "site"

    result = runner.invoke(
        app,
        [
            "render",
            "--bundle",
            str(bundle.root),
            "--corpus",
            str(corpus),
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    html = next((out / "articles").glob("*.html")).read_text(encoding="utf-8")
    assert '<figure class="wiki-figure"' in html
    assert "Schematic overview of an ALD cycle." in html
    assert list((out / "assets" / "figures").glob("*.png"))
