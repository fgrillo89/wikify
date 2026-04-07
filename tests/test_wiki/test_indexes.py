"""Tests for hierarchical index generation (Phase 4)."""

from __future__ import annotations

import json
from unittest.mock import patch

from wikify.wiki.builder import (
    append_unanswered_question,
    generate_domain_index,
    generate_library_catalog,
    generate_theme_index,
    generate_wiki_index,
    write_article,
)
from wikify.wiki.sitemap_data import SitemapEntry, WikiSitemap

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_sitemap(domain: str = "material_science") -> WikiSitemap:
    """Return a WikiSitemap with 1 theme + 2 concepts."""
    theme = SitemapEntry(
        title="ALD Fundamentals",
        slug="ald_fundamentals",
        category="theme",
        scope="Core ALD growth mechanisms.",
        parent_slug=None,
        key_source_ids=["Hub Paper 2020"],
        related_slugs=[],
        depth="full",
        source_types=["paper"],
        domain=domain,
    )
    concept_a = SitemapEntry(
        title="HfO2 Growth Kinetics",
        slug="hfo2_growth_kinetics",
        category="concept",
        scope="GPC, saturation, temperature window.",
        parent_slug="ald_fundamentals",
        key_source_ids=["Smith 2019", "Jones 2020"],
        related_slugs=[],
        depth="draft",
        source_types=["paper"],
        domain=domain,
    )
    concept_b = SitemapEntry(
        title="Precursor Chemistry",
        slug="precursor_chemistry",
        category="concept",
        scope="Volatility, reactivity, thermal stability.",
        parent_slug="ald_fundamentals",
        key_source_ids=["Lee 2021"],
        related_slugs=[],
        depth="stub",
        source_types=["paper"],
        domain=domain,
    )
    return WikiSitemap(entries=[theme, concept_a, concept_b], model="claude-test")


def _empty_graph() -> dict:
    return {"hub_papers": [], "bridge_papers": [], "frontier_papers": [], "full_ranking": []}


def _graph_with_data() -> dict:
    return {
        "hub_papers": [{"id": "h1", "display_name": "Hub Paper 2020"}],
        "bridge_papers": [{"id": "b1", "display_name": "Bridge Paper 2021"}],
        "frontier_papers": [{"id": "f1", "display_name": "Frontier Paper 2022"}],
        "full_ranking": [],
    }


def _patch_no_graph():
    """Patch _load_graph_metrics to return empty (no graph data available)."""
    return patch(
        "wikify.wiki.builder._load_graph_metrics", return_value=_empty_graph()
    )


def _patch_graph():
    """Patch _load_graph_metrics to return valid graph data."""
    return patch(
        "wikify.wiki.builder._load_graph_metrics", return_value=_graph_with_data()
    )


# ---------------------------------------------------------------------------
# generate_theme_index
# ---------------------------------------------------------------------------


class TestGenerateThemeIndex:
    def test_creates_file_in_correct_location(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        expected = tmp_path / "domains" / "material_science" / "_index_ald_fundamentals.md"
        assert out_path == expected
        assert out_path.exists()

    def test_contains_theme_title(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "# Theme: ALD Fundamentals" in content

    def test_contains_domain_in_header(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "material_science" in content

    def test_concepts_table_present(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "## Concepts" in content
        assert "| Article |" in content
        assert "HfO2 Growth Kinetics" in content
        assert "Precursor Chemistry" in content

    def test_scope_appears_in_overview(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "Core ALD growth mechanisms" in content

    def test_graph_highlights_present_when_data_available(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "## Graph Highlights" in content
        assert "Hub Paper 2020" in content

    def test_graph_highlights_absent_when_no_data(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "## Graph Highlights" not in content

    def test_open_questions_from_existing_articles(self, tmp_path):
        """Open questions extracted from concept article frontmatter."""
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]
        concepts = sitemap.concepts()

        # Write a concept article with open_questions
        concepts_dir = tmp_path / "domains" / "material_science" / "concepts"
        write_article(
            path=concepts_dir / "hfo2_growth_kinetics.md",
            title="HfO2 Growth Kinetics",
            content="Body text.",
            sources=["Smith 2019"],
            topics=["ALD"],
            status="draft",
        )
        # Manually add open_questions to frontmatter via raw write
        art_path = concepts_dir / "hfo2_growth_kinetics.md"
        existing = art_path.read_text(encoding="utf-8")
        existing = existing.replace("model: \n", "model: \nopen_questions:\n  - Why does GPC plateau?\n")
        art_path.write_text(existing, encoding="utf-8")

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, concepts)

        content = out_path.read_text(encoding="utf-8")
        assert "## Open Questions in This Theme" in content

    def test_empty_concepts_produces_valid_file(self, tmp_path):
        sitemap = _make_sitemap()
        theme = sitemap.themes()[0]

        with _patch_no_graph():
            out_path = generate_theme_index(tmp_path, "material_science", "ald_fundamentals", theme, [])

        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "## Concepts" in content


# ---------------------------------------------------------------------------
# generate_domain_index
# ---------------------------------------------------------------------------


class TestGenerateDomainIndex:
    def test_creates_file_in_correct_location(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        expected = tmp_path / "domains" / "material_science" / "_index.md"
        assert out_path == expected
        assert out_path.exists()

    def test_contains_domain_title(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        assert "# Material Science Knowledge Base" in content

    def test_themes_table_present(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        assert "## Themes" in content
        assert "| Theme |" in content
        assert "ALD Fundamentals" in content

    def test_header_shows_counts(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        # 1 theme, 2 concepts
        assert "1 themes" in content
        assert "2 concepts" in content

    def test_graph_summary_section_present(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        assert "## Domain Graph Summary" in content

    def test_graph_summary_shows_top_hub_when_available(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        assert "Hub Paper 2020" in content

    def test_open_questions_collected_from_articles(self, tmp_path):
        sitemap = _make_sitemap()

        # Write a domain article with open_questions frontmatter
        domain_dir = tmp_path / "domains" / "material_science" / "concepts"
        art_path = domain_dir / "hfo2_growth_kinetics.md"
        write_article(
            path=art_path,
            title="HfO2 Growth Kinetics",
            content="Body.",
            sources=[],
            topics=[],
            status="draft",
        )
        existing = art_path.read_text(encoding="utf-8")
        existing = existing.replace("model: \n", "model: \nopen_questions:\n  - What limits GPC?\n")
        art_path.write_text(existing, encoding="utf-8")

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        assert "## Open Questions Across Domain" in content

    def test_no_open_questions_section_when_no_articles(self, tmp_path):
        sitemap = _make_sitemap()

        with _patch_no_graph():
            out_path = generate_domain_index(tmp_path, "material_science", sitemap)

        content = out_path.read_text(encoding="utf-8")
        # No articles exist, so no open questions
        assert "## Open Questions Across Domain" not in content


# ---------------------------------------------------------------------------
# generate_library_catalog
# ---------------------------------------------------------------------------


class TestGenerateLibraryCatalog:
    def _domain_info(self, domain="material_science", n_arts=10, n_srcs=50):
        return {
            "domain": domain,
            "article_count": n_arts,
            "source_count": n_srcs,
            "last_updated": "2026-04-01",
            "themes_summary": "ALD, Thin Films",
        }

    def test_creates_index_md_at_root(self, tmp_path):
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        assert out_path == tmp_path / "_index.md"
        assert out_path.exists()

    def test_contains_personal_knowledge_base_header(self, tmp_path):
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "# Personal Knowledge Base" in content

    def test_domains_table_present(self, tmp_path):
        info = [self._domain_info("material_science"), self._domain_info("machine_learning", 8, 30)]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "## Domains" in content
        assert "| Domain |" in content
        assert "Material Science" in content
        assert "Machine Learning" in content

    def test_header_shows_correct_totals(self, tmp_path):
        info = [self._domain_info("a", 10, 50), self._domain_info("b", 5, 20)]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "2 domains" in content
        assert "15 articles" in content
        assert "70 sources" in content

    def test_unanswered_questions_section_always_present(self, tmp_path):
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "## Unanswered Questions" in content

    def test_unanswered_questions_loaded_from_jsonl(self, tmp_path):
        # Write a _unanswered.jsonl file
        jsonl_path = tmp_path / "_unanswered.jsonl"
        records = [
            {"question": "What is the best precursor?", "domain": "material_science", "date": "2026-04-01"},
            {"question": "How does ML help?", "domain": "machine_learning", "date": "2026-04-02"},
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "What is the best precursor?" in content
        assert "How does ML help?" in content

    def test_shows_only_last_10_unanswered(self, tmp_path):
        jsonl_path = tmp_path / "_unanswered.jsonl"
        records = [
            {"question": f"Question {i}", "domain": "d", "date": "2026-04-01"}
            for i in range(15)
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        # Last 10: Question 5 through Question 14
        assert "Question 14" in content
        assert "Question 4" not in content

    def test_no_crash_when_unanswered_missing(self, tmp_path):
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        assert out_path.exists()

    def test_recent_additions_from_articles(self, tmp_path):
        write_article(
            path=tmp_path / "concepts" / "hfo2.md",
            title="HfO2",
            content="Body.",
            sources=[],
            topics=[],
            status="full",
        )
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "## Recent Additions" in content
        assert "HfO2" in content

    def test_empty_domain_list(self, tmp_path):
        out_path = generate_library_catalog(tmp_path, [])
        content = out_path.read_text(encoding="utf-8")
        assert "# Personal Knowledge Base" in content
        assert "0 domains" in content

    def test_cross_domain_connections_from_syntheses_dir(self, tmp_path):
        synth_dir = tmp_path / "syntheses"
        synth_dir.mkdir(parents=True)
        write_article(
            path=synth_dir / "ml_ald_synthesis.md",
            title="ML x ALD Synthesis",
            content="Cross-domain.",
            sources=[],
            topics=[],
            status="draft",
        )
        info = [self._domain_info()]
        out_path = generate_library_catalog(tmp_path, info)
        content = out_path.read_text(encoding="utf-8")
        assert "## Cross-Domain Connections" in content
        assert "ML x ALD Synthesis" in content


# ---------------------------------------------------------------------------
# append_unanswered_question
# ---------------------------------------------------------------------------


class TestAppendUnansweredQuestion:
    def test_creates_file_if_absent(self, tmp_path):
        append_unanswered_question(tmp_path, "What limits GPC?", "material_science")
        assert (tmp_path / "_unanswered.jsonl").exists()

    def test_appended_line_is_valid_json(self, tmp_path):
        append_unanswered_question(tmp_path, "What limits GPC?", "material_science")
        raw = (tmp_path / "_unanswered.jsonl").read_text(encoding="utf-8")
        record = json.loads(raw.strip())
        assert record["question"] == "What limits GPC?"
        assert record["domain"] == "material_science"

    def test_appended_line_has_date_field(self, tmp_path):
        append_unanswered_question(tmp_path, "Test question?", "physics")
        raw = (tmp_path / "_unanswered.jsonl").read_text(encoding="utf-8")
        record = json.loads(raw.strip())
        assert "date" in record
        assert len(record["date"]) == 10  # ISO date: YYYY-MM-DD

    def test_multiple_appends_produce_multiple_lines(self, tmp_path):
        append_unanswered_question(tmp_path, "Q1?", "d1")
        append_unanswered_question(tmp_path, "Q2?", "d2")
        append_unanswered_question(tmp_path, "Q3?", "d3")

        raw = (tmp_path / "_unanswered.jsonl").read_text(encoding="utf-8")
        lines = [l for l in raw.strip().splitlines() if l.strip()]
        assert len(lines) == 3
        questions = [json.loads(l)["question"] for l in lines]
        assert "Q1?" in questions
        assert "Q2?" in questions
        assert "Q3?" in questions

    def test_appends_to_existing_file(self, tmp_path):
        jsonl_path = tmp_path / "_unanswered.jsonl"
        jsonl_path.write_text(
            json.dumps({"question": "existing", "domain": "d", "date": "2026-01-01"}) + "\n",
            encoding="utf-8",
        )
        append_unanswered_question(tmp_path, "new question?", "new_domain")
        lines = [l for l in jsonl_path.read_text(encoding="utf-8").strip().splitlines() if l]
        assert len(lines) == 2
        assert json.loads(lines[1])["question"] == "new question?"

    def test_creates_parent_dirs_if_needed(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        append_unanswered_question(nested, "Q?", "d")
        assert (nested / "_unanswered.jsonl").exists()


# ---------------------------------------------------------------------------
# generate_wiki_index backward-compatibility wrapper
# ---------------------------------------------------------------------------


class TestGenerateWikiIndexBackwardCompat:
    def test_creates_index_md(self, tmp_path):
        content = generate_wiki_index(tmp_path)
        assert (tmp_path / "_index.md").exists()

    def test_returns_string_content(self, tmp_path):
        content = generate_wiki_index(tmp_path)
        assert isinstance(content, str)
        assert "# Knowledge Base Index" in content

    def test_includes_articles_written_to_dir(self, tmp_path):
        concepts_dir = tmp_path / "concepts"
        concepts_dir.mkdir()
        write_article(
            path=concepts_dir / "test_concept.md",
            title="Test Concept",
            content="Body.",
            sources=[],
            topics=[],
            status="full",
        )
        content = generate_wiki_index(tmp_path)
        assert "Test Concept" in content

    def test_writes_content_to_disk(self, tmp_path):
        generate_wiki_index(tmp_path)
        disk_content = (tmp_path / "_index.md").read_text(encoding="utf-8")
        assert "# Knowledge Base Index" in disk_content

    def test_does_not_include_index_files_as_articles(self, tmp_path):
        """Files starting with _ should be excluded from the article listing."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "_sitemap.json").write_text("{}", encoding="utf-8")
        generate_wiki_index(tmp_path)
        content = (tmp_path / "_index.md").read_text(encoding="utf-8")
        assert "_sitemap" not in content
