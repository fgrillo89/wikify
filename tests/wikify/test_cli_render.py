"""Tests for `wikify render` — static-site renderer over a bundle."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.wikify.test_wiki_commit import _setup_validated  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def test_extract_doc_id_does_not_crash_on_ambiguous_handle() -> None:
    """A footnote whose doc short-handle is ambiguous across two docs must
    not crash the render: ``_extract_doc_id`` falls through to the head
    rather than letting ``AmbiguousHandleError`` propagate (regression vs
    the pre-resolver map-lookup path, which could never raise)."""
    from wikify.corpus.handles import build_index
    from wikify.render.html.render import _extract_doc_id

    # Two docs whose ids share the trailing hex -> ambiguous short handle.
    doc_index = build_index(
        ["a_title_c0001_deadbeef", "b_title_c0002_deadbeef"]
    )
    head = "some_chunk (doc:deadbeef)"
    # Must return (no exception); falls through to the unresolved head.
    result = _extract_doc_id(head, doc_meta_map={}, doc_index=doc_index)
    assert result == head


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
    sidecar = (bundle.derived_dir / "site" / "static" / "search-index.js").read_text(
        encoding="utf-8"
    )
    payload = sidecar.removeprefix("window.__WIKI_SEARCH_INDEX__ = ").rstrip().removesuffix(";")
    search_index = json.loads(payload)
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


def test_render_emits_article_graph(tmp_path: Path) -> None:
    """graph.html renders a force-directed view of article wikilinks."""
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    result = runner.invoke(
        app, ["render", "--bundle", str(bundle.root), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    graph_html = (out / "graph.html").read_text(encoding="utf-8")
    assert "Article graph" in graph_html
    assert "d3@7" in graph_html
    assert "Atomic Layer Deposition" in graph_html
    # No topic scaffolding remains in graph_data
    assert "group:" not in graph_html


def test_build_article_graph_data_dedupes_pairs_and_counts_mutual(
    tmp_path: Path,
) -> None:
    """Mutual wikilinks collapse to one edge with weight 2."""
    from dataclasses import dataclass

    from wikify.render.html.render import _build_article_graph_data

    @dataclass
    class _FakePV:
        id: str
        kind: str
        title: str
        url: str

    @dataclass
    class _FakePage:
        id: str
        links: list[str]

    page_views = {
        "A": _FakePV("A", "article", "A", "articles/a.html"),
        "B": _FakePV("B", "article", "B", "articles/b.html"),
        "C": _FakePV("C", "article", "C", "articles/c.html"),
    }
    page_by_id = {
        "A": _FakePage("A", ["B", "C"]),     # A -> B, A -> C
        "B": _FakePage("B", ["A"]),           # B -> A (mutual with A->B)
        "C": _FakePage("C", ["MISSING"]),     # C -> ? (dropped)
    }
    graph = _build_article_graph_data(page_views=page_views, page_by_id=page_by_id)

    assert len(graph["nodes"]) == 3
    pairs = {(edge["source"], edge["target"]): edge["weight"] for edge in graph["links"]}
    assert pairs == {("A", "B"): 2, ("A", "C"): 1}


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
    # Cluster-card overview links to the in-page detailed section anchor.
    assert 'class="cluster-card" href="#group-thin-films"' in html
    assert 'id="group-thin-films"' in html
    assert "1 page</span>" in html  # singular page count on the card
    # Sidebar renders the navigation as a collapsible <details> tree.
    assert "<details" in html
    # When corpus_doc_count is available the template renders "corpus sources cited";
    # otherwise it falls back to "source articles used". Accept either.
    assert "corpus sources cited" in html or "source articles used" in html
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


def test_index_stats_show_corpus_doc_count(tmp_path: Path) -> None:
    """When a corpus is provided, the index page stats show 'corpus sources cited'
    (N of M) instead of the bare source-articles-used count."""
    bundle, _ = _commit_one_article(tmp_path)
    out = tmp_path / "site"
    corpus = tmp_path / "corpus"
    # _commit_one_article builds the corpus at tmp_path/corpus via _setup_validated.
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
    html = (out / "index.html").read_text(encoding="utf-8")
    # Corpus has 2 docs; 1 is cited. Expect "1 of 2 corpus sources cited".
    assert "corpus sources cited" in html


def test_index_stats_resolve_short_doc_hex_handles(tmp_path: Path) -> None:
    """Stats must resolve ``doc:<hex>`` evidence handles to full corpus doc_ids
    so that date_range and words_processed are populated from the DB.

    Uses a corpus whose doc_id ends in a real hex suffix, and evidence
    stored as ``doc:<hex>`` (as written by the workflow).
    """
    from wikify.api import Corpus
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.models import Chunk, Document
    from wikify.render.html.render import _corpus_used_doc_stats

    # Build a minimal corpus with a hex-suffixed doc_id.
    full_doc_id = "[2021 Smith] Test_112233445566"
    hex_suffix = "112233445566"

    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    corpus = Corpus(root=corpus_root)
    corpus.ensure()

    doc = Document(
        id=full_doc_id,
        source_path=f"src/{full_doc_id}.pdf",
        kind="pdf",
        title="Test Paper",
        metadata={"year": 2021},
        markdown_path=f"markdown/{full_doc_id}.md",
        image_dir="images/",
    )
    chunk = Chunk(
        id=f"{full_doc_id}__c0000",
        doc_id=full_doc_id,
        ord=0,
        text="Atomic layer deposition is a thin film technique.",
        char_span=(0, 50),
        section_path=["body"],
        section_type="body",
    )
    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, [doc], {full_doc_id: [chunk]})
    finally:
        store.close()

    # Simulate evidence using short handle form.
    short_handle = f"doc:{hex_suffix}"
    stats = _corpus_used_doc_stats(corpus_root, [short_handle])

    assert stats["total_docs"] == 1
    assert 2021 in stats["years"], f"Year 2021 not resolved; stats={stats}"
    assert stats["words"] is not None and stats["words"] > 0


def test_deduplicate_acronym_glosses_collapses_repeats() -> None:
    """A second ``Expansion (ACR)`` gloss collapses to the bare acronym; the
    first gloss, verbatim reference titles, non-gloss parentheticals, and
    ``[[wikilink]]`` targets are left intact."""
    from wikify.render.html.render import _deduplicate_acronym_glosses

    body = (
        "Atomic layer deposition (ALD) is a technique.[^e1] The method is "
        "self-limiting.[^e2] Atomic layer deposition (ALD) has emerged as "
        "important.[^e3]\n\n## References\n"
        "[^e1]: Atomic layer deposition (ALD) of platinum -- verbatim title\n"
    )
    out = _deduplicate_acronym_glosses(body)
    assert "Atomic layer deposition (ALD) is a technique" in out  # first kept
    assert "ALD has emerged as important" in out                   # 2nd collapsed
    assert "Atomic layer deposition (ALD) has emerged" not in out
    assert "Atomic layer deposition (ALD) of platinum" in out       # ref intact
    # Leading article preserved; only the core term collapses.
    other = "the reaction chamber (RC) was heated; the reaction chamber (RC) cooled"
    assert _deduplicate_acronym_glosses(other) == \
        "the reaction chamber (RC) was heated; the RC cooled"
    # A gloss inside a wikilink target is not rewritten (would break the link).
    wl = ("Atomic layer deposition (ALD) is x. See "
          "[[Atomic layer deposition (ALD)]] then atomic layer deposition (ALD).")
    out_wl = _deduplicate_acronym_glosses(wl)
    assert "[[Atomic layer deposition (ALD)]]" in out_wl  # link target intact
    assert out_wl.endswith("then ALD.")                   # plain repeat collapsed


def test_normalize_math_escapes_fixes_overescaped_commands() -> None:
    r"""Over-escaped ``\\cmd`` inside math collapses to ``\cmd`` while a real
    ``\\`` line break, fenced code, and prose/prices are preserved."""
    from wikify.render.html.render import _normalize_math_escapes

    assert _normalize_math_escapes(r"$$\\gamma_{s} \\quad \\text{x}$$") == \
        r"$$\gamma_{s} \quad \text{x}$$"
    assert _normalize_math_escapes(r"$\\alpha$") == r"$\alpha$"
    assert _normalize_math_escapes(r"$$a \\ b$$") == r"$$a \\ b$$"  # line break kept
    # Prose dollar amounts are not a math span; a stray path backslash is kept.
    assert _normalize_math_escapes(r"costs $5 to $10; C:\\dir kept") == \
        r"costs $5 to $10; C:\\dir kept"
    # Fenced code is excised: example math inside a fence is untouched.
    fenced = "```\n" + r"$$\\gamma$$" + "\n```\n" + r"$$\\gamma$$"
    out = _normalize_math_escapes(fenced)
    assert "```\n" + r"$$\\gamma$$" + "\n```" in out  # code fence intact
    assert out.endswith(r"$$\gamma$$")                # math outside collapsed


def test_clean_evidence_lines_swallows_multiline_quote() -> None:
    """A footnote whose dropped quote carries embedded newlines (a table /
    OCR-broken chunk) must not leak its continuation lines as a stray
    paragraph under ``## References``.
    """
    from wikify.render.html.render import _clean_evidence_lines

    body = (
        "Prose citing a source.[^e1]\n\n"
        "## References\n\n"
        '[^e1]: abc123def456 (doc:abc) > "2\n'
        "O.H\n"
        "2\n"
        'O = H2O UV/thermal stability table dump"\n'
    )
    out = _clean_evidence_lines(
        body, doc_meta_map={}, doc_index=None, kind="article"
    )
    # The garbled quote-continuation lines must NOT appear in the output.
    assert "O.H" not in out
    assert "O = H2O UV/thermal stability table dump" not in out
    # The definition survives (reformatted) and prose/heading are intact.
    assert "[^e1]:" in out
    assert "Prose citing a source." in out
    assert "## References" in out


def test_add_heading_ids_matches_toc_anchors() -> None:
    """Every <h2> must get an id equal to the TOC anchor slug so in-page
    table-of-contents links resolve.
    """
    from wikify.render.html.render import _add_heading_ids, _build_toc, _normalize

    html = (
        "<h2>Mechanism and surface chemistry</h2><p>x</p>"
        "<h2>References</h2>"
    )
    out = _add_heading_ids(html)
    assert 'id="mechanism-and-surface-chemistry"' in out
    assert 'id="references"' in out
    # TOC anchors must match the injected ids.
    ids = {t["id"] for t in _build_toc(out)}
    assert _normalize("Mechanism and surface chemistry") in ids
    assert "references" in ids

    # An existing id is preserved, not doubled.
    html2 = '<h2 id="custom">Title</h2>'
    assert _add_heading_ids(html2).count("id=") == 1


def test_clean_evidence_lines_keeps_prose_after_closed_footnote() -> None:
    """A closed (single-line) footnote must not swallow legitimate prose that
    follows it -- only an unterminated multi-line quote is a continuation.
    """
    from wikify.render.html.render import _clean_evidence_lines

    body = (
        "Intro citing a source.[^e1]\n\n"
        "## References\n\n"
        '[^e1]: h1 (doc:a) > "a clean single-line quote"\n'
        '[^e2]: h2 (doc:b) > "another clean quote"\n'
        "Trailing prose that must survive.\n"
    )
    out = _clean_evidence_lines(body, doc_meta_map={}, doc_index=None, kind="article")
    assert "Trailing prose that must survive." in out
    assert "[^e1]:" in out and "[^e2]:" in out


def test_add_heading_ids_dedupes_and_ignores_attr_value() -> None:
    from wikify.render.html.render import _add_heading_ids, _build_toc

    # Two headings normalising to the same slug get unique ids.
    html = "<h2>Overview</h2><p>a</p><h2>Overview</h2>"
    out = _add_heading_ids(html)
    assert 'id="overview"' in out
    assert 'id="overview-2"' in out
    anchors = [t["id"] for t in _build_toc(out)]
    assert anchors == ["overview", "overview-2"]

    # A data-* attribute whose VALUE contains "id=" must not suppress injection.
    html2 = '<h2 data-x="id=foo">Title</h2>'
    out2 = _add_heading_ids(html2)
    assert 'id="title"' in out2
