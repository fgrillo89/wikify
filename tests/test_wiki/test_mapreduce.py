"""Tests for wiki/mapreduce.py -- map-reduce corpus coverage."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import wikify.wiki.mapreduce as mr_mod

# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_paper(pid="abc123aabbccdd", title="Test Paper", doc_type="paper", year=2024):
    p = MagicMock()
    p.id = pid
    p.title = title
    p.doc_type = doc_type
    p.parsed_authors = ["Smith, J."]
    p.year = year
    p.display_name.return_value = f"Smith {year} - {title}"
    return p


def _graph_metrics_json(hub_ids=None, bridge_ids=None, frontier_ids=None):
    """Build a valid get_graph_metrics() JSON string."""
    hub_ids = hub_ids or []
    bridge_ids = bridge_ids or []
    frontier_ids = frontier_ids or []

    return json.dumps({
        "hub_papers": [{"id": pid, "display_name": f"Hub {pid[:4]}", "pagerank": 0.9} for pid in hub_ids],
        "bridge_papers": [{"id": pid, "display_name": f"Bridge {pid[:4]}", "betweenness": 0.5} for pid in bridge_ids],
        "frontier_papers": [{"id": pid, "display_name": f"Frontier {pid[:4]}"} for pid in frontier_ids],
        "full_ranking": [],
    })


def _make_extraction(
    source_id="abc123aabbccdd",
    display_name="Smith 2024 - Paper",
    doc_type="paper",
    graph_role="standard",
    pagerank_score=0.0,
    extraction="YES: Key claim here.",
    is_relevant=True,
):
    return mr_mod.SourceExtraction(
        source_id=source_id,
        display_name=display_name,
        doc_type=doc_type,
        graph_role=graph_role,
        pagerank_score=pagerank_score,
        extraction=extraction,
        is_relevant=is_relevant,
    )


def _make_exec_result(items):
    """Wrap list in a mock with .all() method like SQLModel exec() results."""
    r = MagicMock()
    r.all.return_value = items
    return r


def _make_session(papers):
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = _make_exec_result(papers)
    return session


# ── _parse_graph_metrics ──────────────────────────────────────────────────────


def test_parse_graph_metrics_extracts_hub_bridge_frontier():
    raw = _graph_metrics_json(
        hub_ids=["hub001aabbccdd"],
        bridge_ids=["brg001aabbccdd"],
        frontier_ids=["frt001aabbccdd"],
    )
    result = mr_mod._parse_graph_metrics(raw)

    assert result["hub001aabbccdd"]["role"] == "hub"
    assert result["hub001aabbccdd"]["pagerank"] == 0.9
    assert result["brg001aabbccdd"]["role"] == "bridge"
    assert result["brg001aabbccdd"]["betweenness"] == 0.5
    assert result["frt001aabbccdd"]["role"] == "frontier"


def test_parse_graph_metrics_returns_empty_on_error():
    assert mr_mod._parse_graph_metrics("not-json") == {}
    assert mr_mod._parse_graph_metrics(json.dumps({"error": "fail"})) == {}


# ── map_chunks_to_topic ───────────────────────────────────────────────────────


def test_map_chunks_to_topic_returns_source_extraction_list():
    """map_chunks_to_topic() should return a list of SourceExtraction objects."""
    pid = "aabbccdd12345678"
    paper = _make_paper(pid=pid, doc_type="paper")
    graph_json = _graph_metrics_json()

    search_result = f"Paper: {pid} | doc_type: paper | title: Test Paper\n"
    digest_result = "Abstract: key finding in this paper."
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "get_graph_metrics", return_value=graph_json),
        patch.object(mr_mod, "search_papers", return_value=search_result),
        patch.object(mr_mod, "read_paper_digest", return_value=digest_result),
        patch.object(mr_mod, "complete", return_value="YES: ALD growth rates increase linearly."),
        patch.object(mr_mod, "get_session", return_value=mock_session),
    ):
        result = mr_mod.map_chunks_to_topic(
            topic_query="ALD growth mechanisms",
            scope="GPC and saturation behavior in ALD",
            domain="material_science",
        )

    assert len(result) == 1
    ext = result[0]
    assert ext.source_id == pid
    assert ext.is_relevant is True
    assert ext.doc_type == "paper"


def test_map_chunks_to_topic_parses_no_as_not_relevant():
    """A haiku response of 'NO' should produce is_relevant=False."""
    pid = "deadbeef56789012"
    paper = _make_paper(pid=pid)
    graph_json = _graph_metrics_json()
    search_result = f"Paper: {pid} | doc_type: paper | title: Test Paper\n"
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "get_graph_metrics", return_value=graph_json),
        patch.object(mr_mod, "search_papers", return_value=search_result),
        patch.object(mr_mod, "read_paper_digest", return_value="Abstract text."),
        patch.object(mr_mod, "complete", return_value="NO"),
        patch.object(mr_mod, "get_session", return_value=mock_session),
    ):
        result = mr_mod.map_chunks_to_topic(
            topic_query="irrelevant topic",
            scope="something unrelated",
            domain="material_science",
        )

    assert len(result) == 1
    assert result[0].is_relevant is False


def test_map_chunks_to_topic_hub_papers_get_hub_role():
    """Papers classified as hub by get_graph_metrics() should receive graph_role='hub'."""
    hub_id = "hubpaper0001abcd"
    paper = _make_paper(pid=hub_id, doc_type="paper")
    graph_json = _graph_metrics_json(hub_ids=[hub_id])
    search_result = f"Paper: {hub_id} | doc_type: paper | title: Hub Paper\n"
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "get_graph_metrics", return_value=graph_json),
        patch.object(mr_mod, "search_papers", return_value=search_result),
        patch.object(mr_mod, "read_paper_digest", return_value="Hub abstract."),
        patch.object(mr_mod, "complete", return_value="YES: hub claim."),
        patch.object(mr_mod, "get_session", return_value=mock_session),
    ):
        result = mr_mod.map_chunks_to_topic(
            topic_query="hub topic",
            scope="hub scope",
            domain="material_science",
        )

    hub_extractions = [e for e in result if e.source_id == hub_id]
    assert len(hub_extractions) == 1
    assert hub_extractions[0].graph_role == "hub"


def test_map_chunks_to_topic_hub_always_included():
    """Hub papers are always added to candidates even if not in search results."""
    hub_id = "hubonly001122aabc"
    regular_id = "regular001122bbcd"
    paper_hub = _make_paper(pid=hub_id, doc_type="paper")
    paper_reg = _make_paper(pid=regular_id, doc_type="paper")

    graph_json = _graph_metrics_json(hub_ids=[hub_id])
    # Search only returns the regular paper
    search_result = f"Paper: {regular_id} | doc_type: paper | title: Regular\n"
    mock_session = _make_session([paper_hub, paper_reg])

    with (
        patch.object(mr_mod, "get_graph_metrics", return_value=graph_json),
        patch.object(mr_mod, "search_papers", return_value=search_result),
        patch.object(mr_mod, "read_paper_digest", return_value="Abstract."),
        patch.object(mr_mod, "complete", return_value="YES: relevant."),
        patch.object(mr_mod, "get_session", return_value=mock_session),
    ):
        result = mr_mod.map_chunks_to_topic(
            topic_query="topic",
            scope="scope",
            domain="material_science",
        )

    source_ids = {e.source_id for e in result}
    assert hub_id in source_ids, "Hub paper must always be included even if not in search results"


# ── reduce_to_article ─────────────────────────────────────────────────────────


def test_reduce_to_article_uses_persona_in_system_prompt():
    """reduce_to_article() must prepend the persona text to the system prompt."""
    persona_text = "You are a senior ALD process engineer."
    extractions = [_make_extraction(doc_type="paper", graph_role="hub")]

    with patch.object(mr_mod, "complete", return_value="Article body.") as mock_complete:
        mr_mod.reduce_to_article(
            topic="ALD Growth Kinetics",
            scope="GPC and saturation",
            domain="material_science",
            extractions=extractions,
            persona=persona_text,
            status="draft",
        )

    messages = mock_complete.call_args[1]["messages"]
    system_msg = next(m["content"] for m in messages if m["role"] == "system")
    assert persona_text in system_msg


def test_reduce_to_article_academic_zone_labels():
    """When >60% of relevant sources are papers, use 'academic' zone labels."""
    extractions = [
        _make_extraction(doc_type="paper", source_id=f"p{i:04d}aabbccddee") for i in range(7)
    ] + [
        _make_extraction(doc_type="web_article", source_id=f"w{i:04d}aabbccddee") for i in range(3)
    ]

    with patch.object(mr_mod, "complete", return_value="Body text.") as mock_complete:
        mr_mod.reduce_to_article(
            topic="Topic",
            scope="scope",
            domain="academic_domain",
            extractions=extractions,
            persona="You are a researcher.",
        )

    messages = mock_complete.call_args[1]["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "What Is Known" in user_msg


def test_reduce_to_article_practice_zone_labels():
    """When >60% of relevant sources are web_article/markdown, use 'practice' labels."""
    extractions = [
        _make_extraction(doc_type="web_article", source_id=f"w{i:04d}aabbccddee") for i in range(7)
    ] + [
        _make_extraction(doc_type="paper", source_id=f"p{i:04d}aabbccddee") for i in range(2)
    ]

    with patch.object(mr_mod, "complete", return_value="Body text.") as mock_complete:
        mr_mod.reduce_to_article(
            topic="Practitioner Guide",
            scope="scope",
            domain="engineering",
            extractions=extractions,
            persona="You are a practitioner.",
        )

    messages = mock_complete.call_args[1]["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Practitioner Consensus" in user_msg


def test_reduce_to_article_design_domain_uses_design_labels():
    """When 'design' appears in the domain name, use 'design' zone labels."""
    extractions = [_make_extraction(doc_type="paper")]

    with patch.object(mr_mod, "complete", return_value="Body.") as mock_complete:
        mr_mod.reduce_to_article(
            topic="Color Theory",
            scope="principles",
            domain="interior_design",
            extractions=extractions,
            persona="You are a designer.",
        )

    messages = mock_complete.call_args[1]["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Established Principles" in user_msg


def test_reduce_to_article_builds_evidence_block_with_role_prefixes():
    """Evidence block must include [HUB], [BRIDGE], [FRONTIER] prefixes."""
    extractions = [
        _make_extraction(source_id="hub00001122aabbcc", graph_role="hub", extraction="YES: Hub claim."),
        _make_extraction(source_id="brg00001122aabbcc", graph_role="bridge", extraction="YES: Bridge claim."),
        _make_extraction(source_id="frt00001122aabbcc", graph_role="frontier", extraction="YES: Frontier claim."),
    ]

    with patch.object(mr_mod, "complete", return_value="Body.") as mock_complete:
        mr_mod.reduce_to_article(
            topic="Topic",
            scope="scope",
            domain="material_science",
            extractions=extractions,
            persona="Persona.",
        )

    messages = mock_complete.call_args[1]["messages"]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "[HUB]" in user_msg
    assert "[BRIDGE]" in user_msg
    assert "[FRONTIER]" in user_msg


# ── record_coverage ───────────────────────────────────────────────────────────


def test_record_coverage_writes_correct_number_of_rows():
    """record_coverage() should insert one SourceCoverage row per relevant extraction."""
    extractions = [
        _make_extraction(source_id="src001aabbccddee", extraction="YES: claim A.", is_relevant=True),
        _make_extraction(source_id="src002aabbccddee", extraction="NO", is_relevant=False),
        _make_extraction(source_id="src003aabbccddee", extraction="YES: claim B.", is_relevant=True),
    ]

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(mr_mod, "get_session", return_value=mock_session),
        patch.object(mr_mod, "SourceCoverage") as mock_sc_cls,
    ):
        count = mr_mod.record_coverage(
            article_slug="ald_growth_kinetics",
            domain="material_science",
            extractions=extractions,
        )

    assert count == 2  # only 2 relevant
    assert mock_sc_cls.call_count == 2
    assert mock_session.add.call_count == 2
    mock_session.commit.assert_called_once()


def test_record_coverage_returns_zero_when_no_relevant():
    """record_coverage() should return 0 and write no rows when nothing is relevant."""
    extractions = [
        _make_extraction(is_relevant=False),
        _make_extraction(is_relevant=False),
    ]

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(mr_mod, "get_session", return_value=mock_session),
        patch.object(mr_mod, "SourceCoverage") as mock_sc_cls,
    ):
        count = mr_mod.record_coverage("some_slug", "domain", extractions)

    assert count == 0
    mock_sc_cls.assert_not_called()
    mock_session.add.assert_not_called()


def test_record_coverage_sets_correct_fields():
    """SourceCoverage rows must carry article_slug, source_id, domain, extraction."""
    ext = _make_extraction(
        source_id="src001aabbccddee",
        extraction="YES: specific claim.",
        is_relevant=True,
    )

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    captured_kwargs: dict = {}

    def capture_sc(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with (
        patch.object(mr_mod, "get_session", return_value=mock_session),
        patch.object(mr_mod, "SourceCoverage", side_effect=capture_sc),
    ):
        mr_mod.record_coverage("my_article", "my_domain", [ext])

    assert captured_kwargs["article_slug"] == "my_article"
    assert captured_kwargs["source_id"] == "src001aabbccddee"
    assert captured_kwargs["domain"] == "my_domain"
    assert "YES: specific claim." in captured_kwargs["extraction"]
