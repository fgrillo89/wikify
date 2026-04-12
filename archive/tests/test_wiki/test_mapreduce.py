"""Tests for wiki/mapreduce.py -- map-reduce corpus coverage."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import wikify.wiki.mapreduce as mr_mod
from wikify.core.corpus_tools import CorpusGraphMetrics, CorpusSearchResult

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


def _graph_metrics(hub_ids=None, bridge_ids=None, frontier_ids=None) -> CorpusGraphMetrics:
    hub_ids = list(hub_ids or [])
    bridge_ids = list(bridge_ids or [])
    frontier_ids = list(frontier_ids or [])
    by_paper: dict[str, dict] = {}
    for pid in hub_ids:
        by_paper[pid] = {"role": "hub", "pagerank": 0.9, "betweenness": 0.0}
    for pid in bridge_ids:
        by_paper[pid] = {"role": "bridge", "pagerank": 0.0, "betweenness": 0.5}
    for pid in frontier_ids:
        by_paper[pid] = {"role": "frontier", "pagerank": 0.0, "betweenness": 0.0}
    return CorpusGraphMetrics(
        by_paper=by_paper,
        hub_ids=hub_ids,
        bridge_ids=bridge_ids,
        frontier_ids=frontier_ids,
    )


def _search_result(paper_ids: list[str]) -> CorpusSearchResult:
    return CorpusSearchResult(
        paper_ids=list(paper_ids),
        text="\n".join(f"Paper: {p}" for p in paper_ids),
        total_papers=len(paper_ids),
        total_chunks=len(paper_ids),
        total_tokens=100,
    )


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
    r = MagicMock()
    r.all.return_value = items
    return r


def _make_session(papers):
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = _make_exec_result(papers)
    return session


# ── map_chunks_to_topic ───────────────────────────────────────────────────────


def test_map_chunks_to_topic_returns_source_extraction_list():
    pid = "aabbccdd12345678"
    paper = _make_paper(pid=pid, doc_type="paper")
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "compute_graph_metrics", return_value=_graph_metrics()),
        patch.object(mr_mod, "search_corpus", return_value=_search_result([pid])),
        patch.object(mr_mod, "read_paper_digest_text", return_value="Abstract."),
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
    pid = "deadbeef56789012"
    paper = _make_paper(pid=pid)
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "compute_graph_metrics", return_value=_graph_metrics()),
        patch.object(mr_mod, "search_corpus", return_value=_search_result([pid])),
        patch.object(mr_mod, "read_paper_digest_text", return_value="Abstract text."),
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
    hub_id = "hubpaper0001abcd"
    paper = _make_paper(pid=hub_id, doc_type="paper")
    mock_session = _make_session([paper])

    with (
        patch.object(mr_mod, "compute_graph_metrics", return_value=_graph_metrics(hub_ids=[hub_id])),
        patch.object(mr_mod, "search_corpus", return_value=_search_result([hub_id])),
        patch.object(mr_mod, "read_paper_digest_text", return_value="Hub abstract."),
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
    hub_id = "hubonly001122aabc"
    regular_id = "regular001122bbcd"
    paper_hub = _make_paper(pid=hub_id, doc_type="paper")
    paper_reg = _make_paper(pid=regular_id, doc_type="paper")
    mock_session = _make_session([paper_hub, paper_reg])

    with (
        patch.object(mr_mod, "compute_graph_metrics", return_value=_graph_metrics(hub_ids=[hub_id])),
        patch.object(mr_mod, "search_corpus", return_value=_search_result([regular_id])),
        patch.object(mr_mod, "read_paper_digest_text", return_value="Abstract."),
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
