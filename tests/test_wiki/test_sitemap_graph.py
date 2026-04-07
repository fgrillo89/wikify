"""Tests for graph-aware sitemap generation (Phase 3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from wikify.wiki.legacy.sitemap import (
    SitemapEntry,
    WikiSitemap,
    _build_graph_context_block,
    _classify_paper_domain,
    _topics_to_domain,
    explore_corpus_for_sitemap,
    generate_multi_domain_sitemap,
    generate_sitemap,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _graph_metrics_json(
    hubs=None,
    bridges=None,
    frontiers=None,
    error=None,
) -> str:
    data: dict = {
        "hub_papers": hubs or [{"id": "h1", "display_name": "Hub Paper 2020"}],
        "bridge_papers": bridges or [{"id": "b1", "display_name": "Bridge Paper 2021"}],
        "frontier_papers": frontiers or [{"id": "f1", "display_name": "Frontier Paper 2022"}],
        "full_ranking": [],
    }
    if error is not None:
        data["error"] = error
    return json.dumps(data)


def _make_paper(pid="p1", title="Test Paper", source_path="", doc_type="paper"):
    p = MagicMock()
    p.id = pid
    p.title = title
    p.source_path = source_path
    p.doc_type = doc_type
    p.display_name.return_value = f"Author 2024 - {title}"
    return p


def _make_topic(paper_id="p1", topic="material_science"):
    t = MagicMock()
    t.paper_id = paper_id
    t.topic = topic
    return t


def _make_exec_result(items):
    r = MagicMock()
    r.all.return_value = items
    return r


def _make_session(exec_side_effects=None):
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    if exec_side_effects is not None:
        session.exec.side_effect = [_make_exec_result(items) for items in exec_side_effects]
    return session


def _make_agent_result(content="Exploration summary."):
    result = MagicMock()
    result.content = content
    result.tool_calls = []
    return result


def _minimal_sitemap_json() -> str:
    return json.dumps(
        {
            "entries": [
                {
                    "title": "ALD Fundamentals",
                    "slug": "ald_fundamentals",
                    "category": "theme",
                    "scope": "Core ALD mechanisms.",
                    "parent_slug": None,
                    "key_source_ids": ["Hub Paper 2020"],
                    "related_slugs": [],
                    "depth": "full",
                    "source_types": ["paper"],
                    "notes": "",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# _build_graph_context_block
# ---------------------------------------------------------------------------


class TestBuildGraphContextBlock:
    def test_returns_block_with_hub_bridge_frontier(self):
        block = _build_graph_context_block(_graph_metrics_json())
        assert "Hub Paper 2020" in block
        assert "Bridge Paper 2021" in block
        assert "Frontier Paper 2022" in block

    def test_returns_empty_on_json_error(self):
        block = _build_graph_context_block("NOT JSON")
        assert block == ""

    def test_returns_empty_on_error_key(self):
        block = _build_graph_context_block(_graph_metrics_json(error="corpus too small"))
        assert block == ""

    def test_returns_empty_when_all_lists_empty(self):
        json_str = json.dumps(
            {"hub_papers": [], "bridge_papers": [], "frontier_papers": [], "full_ranking": []}
        )
        block = _build_graph_context_block(json_str)
        assert block == ""

    def test_limits_to_10_entries(self):
        hubs = [{"id": f"h{i}", "display_name": f"Hub {i}"} for i in range(20)]
        block = _build_graph_context_block(_graph_metrics_json(hubs=hubs))
        # Should not contain Hub 10+ (index 10 and above)
        assert "Hub 10" not in block
        assert "Hub 9" in block

    def test_includes_planning_guidance(self):
        block = _build_graph_context_block(_graph_metrics_json())
        assert "HUB papers belong as key_source_ids in THEME articles" in block
        assert "BRIDGE papers belong in SYNTHESIS articles" in block
        assert "FRONTIER papers inform Open Questions" in block


# ---------------------------------------------------------------------------
# explore_corpus_for_sitemap -- graph context injection
# ---------------------------------------------------------------------------


class TestExploreCorpusForSitemapGraphContext:
    """Verify that explore_corpus_for_sitemap injects graph context into system prompt."""

    # Patch targets: deferred imports live in their source modules; patch there.
    _GRAPH_METRICS = "wikify.papers.agent.tools.get_graph_metrics"
    _FIND_GAPS = "wikify.papers.agent.tools.find_corpus_gaps"
    _FIND_SYNTH = "wikify.papers.agent.tools.find_synthesis_opportunities"
    _AGENT_CLS = "wikify.papers.agent.core.ScholarForgeAgent"
    _HOOKS = "wikify.papers.agent.defaults.get_default_hooks"
    _TOOLS = "wikify.papers.agent.defaults.get_explorer_tools"
    _CREATE_CTX = "wikify.papers.agent.run_context.create_run_context"
    _USE_CTX = "wikify.papers.agent.run_context.use_run_context"

    def _run_explore(self, graph_json, agent_result=None, gaps="gaps text", synth="synth text"):
        """Helper that patches all external calls and runs explore_corpus_for_sitemap."""
        if agent_result is None:
            agent_result = _make_agent_result()

        mock_agent_instance = MagicMock()
        mock_agent_instance.run.return_value = agent_result

        mock_agent_cls = MagicMock(return_value=mock_agent_instance)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(self._GRAPH_METRICS, return_value=graph_json),
            patch(self._FIND_GAPS, return_value=gaps),
            patch(self._FIND_SYNTH, return_value=synth),
            patch(self._AGENT_CLS, mock_agent_cls),
            patch(self._HOOKS, return_value=MagicMock()),
            patch(self._TOOLS, return_value=[]),
            patch(self._CREATE_CTX, return_value=MagicMock()),
            patch(self._USE_CTX, return_value=mock_ctx),
        ):
            content, ids = explore_corpus_for_sitemap(
                topic_hint="",
                model=None,
                max_papers=5,
                run_context=None,
            )
        return content, ids, mock_agent_cls

    def test_graph_context_prepended_to_system_prompt(self):
        """When graph metrics succeed, system prompt starts with graph context."""
        graph_json = _graph_metrics_json()
        _, _, mock_agent_cls = self._run_explore(graph_json)

        call_kwargs = mock_agent_cls.call_args[1]
        sys_prompt: str = call_kwargs["system_prompt"]
        assert "Hub Paper 2020" in sys_prompt
        assert "Bridge Paper 2021" in sys_prompt
        assert "Frontier Paper 2022" in sys_prompt
        # Graph context should come BEFORE original instructions
        assert sys_prompt.index("Hub Paper 2020") < sys_prompt.index("corpus structure analyst")

    def test_proceeds_without_graph_when_metrics_fail(self):
        """When get_graph_metrics raises, exploration proceeds without graph context."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.run.return_value = _make_agent_result()
        mock_agent_cls = MagicMock(return_value=mock_agent_instance)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch(self._GRAPH_METRICS, side_effect=RuntimeError("no graph")),
            patch(self._FIND_GAPS, return_value=""),
            patch(self._FIND_SYNTH, return_value=""),
            patch(self._AGENT_CLS, mock_agent_cls),
            patch(self._HOOKS, return_value=MagicMock()),
            patch(self._TOOLS, return_value=[]),
            patch(self._CREATE_CTX, return_value=MagicMock()),
            patch(self._USE_CTX, return_value=mock_ctx),
        ):
            content, ids = explore_corpus_for_sitemap(
                topic_hint="",
                model=None,
                max_papers=5,
                run_context=None,
            )
        assert content == "Exploration summary."

    def test_proceeds_when_graph_returns_empty_lists(self):
        empty_json = json.dumps(
            {"hub_papers": [], "bridge_papers": [], "frontier_papers": [], "full_ranking": []}
        )
        _, _, mock_agent_cls = self._run_explore(empty_json)
        call_kwargs = mock_agent_cls.call_args[1]
        sys_prompt: str = call_kwargs["system_prompt"]
        # No graph block -- original system prompt used as-is
        assert sys_prompt.startswith("You are a corpus structure analyst")

    def test_gap_and_synthesis_context_in_user_message(self):
        """Corpus gap and synthesis opportunity text appears in the user message."""
        graph_json = _graph_metrics_json()
        _, _, mock_agent_cls = self._run_explore(
            graph_json, gaps="gap: ALD thermal budget", synth="synth: HfO2 + ML"
        )
        agent_instance = mock_agent_cls.return_value
        prompt_arg = agent_instance.run.call_args[0][0]
        assert "gap: ALD thermal budget" in prompt_arg
        assert "synth: HfO2 + ML" in prompt_arg

    def test_returns_content_and_empty_ids_when_no_tool_calls(self):
        result = _make_agent_result("summary text")
        content, ids, _ = self._run_explore(_graph_metrics_json(), agent_result=result)
        assert content == "summary text"
        assert ids == []


# ---------------------------------------------------------------------------
# generate_sitemap -- domain parameter
# ---------------------------------------------------------------------------


class TestGenerateSitemapDomain:
    # Deferred imports in generate_sitemap:
    _EXPLORE = "wikify.wiki.legacy.sitemap.explore_corpus_for_sitemap"
    _COMPLETE = "wikify.llm.client.complete"
    _SETTINGS = "wikify.config.settings"

    def _run_generate(self, domain="", tmp_path=None):
        if tmp_path is None:
            import tempfile

            tmp_path = Path(tempfile.mkdtemp())

        mock_exploration = ("Exploration summary.", [])
        mock_complete_return = _minimal_sitemap_json()

        mock_settings = MagicMock()
        mock_settings.llm_model = "claude-test"

        with (
            patch(self._EXPLORE, return_value=mock_exploration),
            patch(self._COMPLETE, return_value=mock_complete_return),
            patch(self._SETTINGS, mock_settings),
        ):
            sitemap = generate_sitemap(
                topic_hint="",
                model=None,
                wiki_dir=tmp_path,
                max_explore_papers=5,
                run_context=None,
                domain=domain,
            )
        return sitemap

    def test_domain_stored_on_entries(self, tmp_path):
        sitemap = self._run_generate(domain="material_science", tmp_path=tmp_path)
        for entry in sitemap.entries:
            assert entry.domain == "material_science"

    def test_empty_domain_stored_as_empty_string(self, tmp_path):
        sitemap = self._run_generate(domain="", tmp_path=tmp_path)
        for entry in sitemap.entries:
            assert entry.domain == ""

    def test_domain_included_in_user_message(self, tmp_path):
        captured_msgs = []

        def _capture_complete(messages, **kwargs):
            captured_msgs.extend(messages)
            return _minimal_sitemap_json()

        mock_settings = MagicMock()
        mock_settings.llm_model = "claude-test"

        with (
            patch(self._EXPLORE, return_value=("Exploration.", [])),
            patch(self._COMPLETE, side_effect=_capture_complete),
            patch(self._SETTINGS, mock_settings),
        ):
            generate_sitemap(
                topic_hint="",
                model=None,
                wiki_dir=tmp_path,
                max_explore_papers=5,
                run_context=None,
                domain="material_science",
            )

        user_msg = next(m["content"] for m in captured_msgs if m["role"] == "user")
        assert "material_science" in user_msg

    def test_sitemap_saved_to_disk(self, tmp_path):
        self._run_generate(domain="", tmp_path=tmp_path)
        assert (tmp_path / "_sitemap.json").exists()

    def test_invalid_depth_defaults_to_draft(self, tmp_path):
        bad_json = json.dumps(
            {
                "entries": [
                    {
                        "title": "Bad Depth",
                        "slug": "bad_depth",
                        "category": "concept",
                        "scope": "test",
                        "parent_slug": "theme_x",
                        "key_source_ids": [],
                        "related_slugs": [],
                        "depth": "INVALID",
                        "source_types": [],
                        "notes": "",
                    }
                ]
            }
        )
        mock_settings = MagicMock()
        mock_settings.llm_model = "claude-test"

        with (
            patch(self._EXPLORE, return_value=("Exploration.", [])),
            patch(self._COMPLETE, return_value=bad_json),
            patch(self._SETTINGS, mock_settings),
        ):
            sitemap = generate_sitemap(
                topic_hint="",
                model=None,
                wiki_dir=tmp_path,
                max_explore_papers=5,
                run_context=None,
            )
        assert sitemap.entries[0].depth == "draft"


# ---------------------------------------------------------------------------
# _classify_paper_domain and _topics_to_domain
# ---------------------------------------------------------------------------


class TestDomainClassification:
    def test_path_keyword_material_science(self):
        p = _make_paper(source_path="/data/material_science/paper.pdf")
        assert _classify_paper_domain(p) == "material_science"

    def test_path_keyword_machine_learning(self):
        p = _make_paper(source_path="/data/machine_learning/paper.pdf")
        assert _classify_paper_domain(p) == "machine_learning"

    def test_path_no_match_returns_empty(self):
        p = _make_paper(source_path="/data/random_stuff/paper.pdf")
        assert _classify_paper_domain(p) == ""

    def test_topics_to_domain_ald_maps_to_material_science(self):
        domain = _topics_to_domain(["ALD", "thin film deposition"])
        assert domain == "material_science"

    def test_topics_to_domain_ml_maps_to_machine_learning(self):
        domain = _topics_to_domain(["deep learning", "neural networks"])
        assert domain == "machine_learning"

    def test_topics_to_domain_fallback_uses_first_topic(self):
        # "tribology" doesn't match any keyword dict entry, falls back to first topic
        domain = _topics_to_domain(["tribology"])
        assert domain == "tribology"

    def test_topics_to_domain_empty_returns_general(self):
        domain = _topics_to_domain([])
        assert domain == "general"


# ---------------------------------------------------------------------------
# generate_multi_domain_sitemap -- domain grouping and synthesis detection
# ---------------------------------------------------------------------------


class TestGenerateMultiDomainSitemap:
    # Deferred imports in generate_multi_domain_sitemap live in these modules:
    _GET_SESSION = "wikify.store.db.get_session"
    _FIND_SYNTH = "wikify.papers.agent.tools.find_synthesis_opportunities"
    # generate_sitemap is in the same module -- patch the module-level name
    _GEN_SITEMAP = "wikify.wiki.legacy.sitemap.generate_sitemap"

    def _make_papers_and_topics(self):
        """Return 6 material_science papers and 6 machine_learning papers + topics."""
        ms_papers = [
            _make_paper(
                pid=f"ms{i}",
                title=f"MS Paper {i}",
                source_path=f"/data/material_science/p{i}.pdf",
            )
            for i in range(6)
        ]
        ml_papers = [
            _make_paper(
                pid=f"ml{i}",
                title=f"ML Paper {i}",
                source_path=f"/data/machine_learning/p{i}.pdf",
            )
            for i in range(6)
        ]
        all_papers = ms_papers + ml_papers
        topics = (
            [_make_topic(paper_id=f"ms{i}", topic="material_science") for i in range(6)]
            + [_make_topic(paper_id=f"ml{i}", topic="machine_learning") for i in range(6)]
        )
        return all_papers, topics

    def _minimal_sitemap_for_domain(self, domain: str) -> WikiSitemap:
        entry = SitemapEntry(
            title=f"{domain} Theme",
            slug=f"{domain}_theme",
            category="theme",
            scope=f"Theme for {domain}.",
            parent_slug=None,
            key_source_ids=[f"Paper from {domain}"],
            related_slugs=[],
            depth="full",
            source_types=["paper"],
            domain=domain,
        )
        return WikiSitemap(entries=[entry], model="claude-test")

    def test_groups_papers_by_domain_and_generates_sitemaps(self, tmp_path):
        papers, topics = self._make_papers_and_topics()
        session = _make_session(exec_side_effects=[papers, topics])

        ms_sitemap = self._minimal_sitemap_for_domain("material_science")
        ml_sitemap = self._minimal_sitemap_for_domain("machine_learning")

        call_count = 0

        def _fake_generate(topic_hint, model, wiki_dir, max_explore_papers, run_context, domain=""):
            nonlocal call_count
            call_count += 1
            if "material_science" in str(wiki_dir):
                return ms_sitemap
            return ml_sitemap

        with (
            patch(self._GET_SESSION, return_value=session),
            patch(self._GEN_SITEMAP, side_effect=_fake_generate),
            patch(self._FIND_SYNTH, return_value=""),
        ):
            result = generate_multi_domain_sitemap(tmp_path, model=None, max_explore_papers=5)

        assert "material_science" in result
        assert "machine_learning" in result
        assert call_count == 2

    def test_skips_domains_with_fewer_than_5_papers(self, tmp_path):
        # Only 3 papers for "chemistry" domain, 6 for material_science
        small_papers = [
            _make_paper(
                pid=f"sm{i}",
                title=f"Small {i}",
                source_path=f"/data/chemistry/p{i}.pdf",
            )
            for i in range(3)
        ]
        ms_papers = [
            _make_paper(
                pid=f"ms{i}",
                title=f"MS {i}",
                source_path=f"/data/material_science/p{i}.pdf",
            )
            for i in range(6)
        ]
        topics: list = []
        session = _make_session(exec_side_effects=[small_papers + ms_papers, topics])

        ms_sitemap = self._minimal_sitemap_for_domain("material_science")

        with (
            patch(self._GET_SESSION, return_value=session),
            patch(self._GEN_SITEMAP, return_value=ms_sitemap),
            patch(self._FIND_SYNTH, return_value=""),
        ):
            result = generate_multi_domain_sitemap(tmp_path, model=None)

        assert "material_science" in result
        assert "chemistry" not in result

    def test_cross_domain_synthesis_entry_added_when_sources_match(self, tmp_path):
        papers, topics = self._make_papers_and_topics()
        session = _make_session(exec_side_effects=[papers, topics])

        ms_sitemap = self._minimal_sitemap_for_domain("material_science")
        ml_sitemap = self._minimal_sitemap_for_domain("machine_learning")

        def _fake_generate(topic_hint, model, wiki_dir, max_explore_papers, run_context, domain=""):
            if "material_science" in str(wiki_dir):
                return ms_sitemap
            return ml_sitemap

        # Synthesis text mentions sources from both domains
        synth_text = "Paper from material_science ... Paper from machine_learning"

        with (
            patch(self._GET_SESSION, return_value=session),
            patch(self._GEN_SITEMAP, side_effect=_fake_generate),
            patch(self._FIND_SYNTH, return_value=synth_text),
        ):
            result = generate_multi_domain_sitemap(tmp_path, model=None)

        # Both sitemaps should have a synthesis entry added
        ms_cats = [e.category for e in result["material_science"].entries]
        ml_cats = [e.category for e in result["machine_learning"].entries]
        assert "synthesis" in ms_cats
        assert "synthesis" in ml_cats

    def test_cross_domain_synthesis_not_added_when_no_source_overlap(self, tmp_path):
        papers, topics = self._make_papers_and_topics()
        session = _make_session(exec_side_effects=[papers, topics])

        ms_sitemap = self._minimal_sitemap_for_domain("material_science")
        ml_sitemap = self._minimal_sitemap_for_domain("machine_learning")

        def _fake_generate(topic_hint, model, wiki_dir, max_explore_papers, run_context, domain=""):
            if "material_science" in str(wiki_dir):
                return ms_sitemap
            return ml_sitemap

        # Synthesis text does NOT mention sources from either domain
        synth_text = "unrelated content"

        with (
            patch(self._GET_SESSION, return_value=session),
            patch(self._GEN_SITEMAP, side_effect=_fake_generate),
            patch(self._FIND_SYNTH, return_value=synth_text),
        ):
            result = generate_multi_domain_sitemap(tmp_path, model=None)

        ms_cats = [e.category for e in result["material_science"].entries]
        ml_cats = [e.category for e in result["machine_learning"].entries]
        assert "synthesis" not in ms_cats
        assert "synthesis" not in ml_cats
