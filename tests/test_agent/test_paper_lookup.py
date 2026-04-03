"""Regression tests for paper lookup across agent tool entry points."""

from __future__ import annotations

import json

from wikify.agent.scripted import scripted_explore
from wikify.agent.tools import deep_read, read_paper_digest
from wikify.store import db as db_module
from wikify.store.db import DatabaseManager
from wikify.store.models import Chunk, Paper


def _seed_paper_corpus(monkeypatch, tmp_path) -> dict[str, str]:
    """Create a temporary corpus with one paper and two chunks."""
    manager = DatabaseManager(db_path=str(tmp_path / "papers.db"))
    monkeypatch.setattr(db_module, "_db", manager)
    paper_id = "paper-1"
    title = "Neuromorphic Memristors for Edge AI"
    display_name = "Kim 2021 - Neuromorphic Memristors for Edge AI"

    paper = Paper(
        id=paper_id,
        title=title,
        authors=json.dumps(["Alice Kim", "Bob Lee"]),
        summary="A survey of memristor devices for efficient inference.",
        year=2021,
        doi="10.1000/example",
        section_tree="{}",
        section_summaries='{"1.Introduction": "Sets up the edge-AI motivation."}',
    )

    with manager.session() as session:
        session.add(paper)
        session.add(
            Chunk(
                id="chunk-1",
                paper_id=paper.id,
                section_path="1.Introduction",
                content="Introduction text with edge-AI context.",
                token_count=8,
                chunk_index=0,
            )
        )
        session.add(
            Chunk(
                id="chunk-2",
                paper_id=paper.id,
                section_path="5.Conclusion",
                content="Conclusion text with efficiency findings.",
                token_count=7,
                chunk_index=1,
            )
        )
        session.commit()

    return {
        "id": paper_id,
        "title": title,
        "display_name": display_name,
    }


def test_deep_read_matches_truncated_display_name(monkeypatch, tmp_path):
    paper = _seed_paper_corpus(monkeypatch, tmp_path)

    raw = deep_read(paper["display_name"][:24])
    data = json.loads(raw)

    assert data["paper"]["display_name"] == paper["display_name"]
    assert "Introduction text with edge-AI context." in data["full_text"]
    assert data["match_count"] == 1
    assert data["ok"] is True
    assert "error" not in data


def test_read_paper_digest_matches_display_name(monkeypatch, tmp_path):
    paper = _seed_paper_corpus(monkeypatch, tmp_path)

    digest = read_paper_digest(paper["display_name"][:24])

    assert paper["title"] in digest
    assert "Section Summaries" in digest
    assert "edge-AI motivation" in digest


def test_deep_read_reports_lookup_failures_explicitly(monkeypatch, tmp_path):
    _seed_paper_corpus(monkeypatch, tmp_path)

    data = json.loads(deep_read("paper that is not here"))

    assert data["paper"] is None
    assert data["full_text"] == ""
    assert data["match_count"] == 0
    assert data["ok"] is False
    assert data["error"] == "No paper found matching: 'paper that is not here'"


def test_scripted_explore_uses_title_pattern_and_surfaces_deep_read_fallback(monkeypatch, tmp_path):
    paper = _seed_paper_corpus(monkeypatch, tmp_path)
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        "wikify.agent.reading_log.reset_reading_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "wikify.agent.concept_graph.reset_concept_graph", lambda *args, **kwargs: None
    )
    monkeypatch.setattr("wikify.agent.tools.reset_paper_summaries", lambda: None)
    monkeypatch.setattr(
        "wikify.evaluate.frontier.frontier_exploration_order",
        lambda max_papers=12: [(paper["id"], "full", "Seed paper for review")],
    )
    monkeypatch.setattr("wikify.agent.tools.find_corpus_gaps", lambda: "gaps")
    monkeypatch.setattr(
        "wikify.agent.tools.find_synthesis_opportunities", lambda: "synthesis"
    )

    def fake_deep_read(pattern: str, reason: str = "") -> str:
        seen["pattern"] = pattern
        return json.dumps(
            {
                "paper": None,
                "full_text": "",
                "token_count": 0,
                "match_count": 0,
                "error": "No paper found matching: 'seed paper'",
            }
        )

    monkeypatch.setattr("wikify.agent.tools.deep_read", fake_deep_read)
    monkeypatch.setattr(
        "wikify.agent.tools.read_paper_digest",
        lambda pattern, reason="": f"DIGEST FALLBACK for {pattern}",
    )

    result = scripted_explore(max_papers=1, n_deep=1, topic="edge AI")

    assert seen["pattern"] == paper["title"][:40]
    assert result["papers"][0]["depth"] == "digest"
    assert result["papers"][0]["warning"] == "No paper found matching: 'seed paper'"
    assert result["papers"][0]["text"] == f"DIGEST FALLBACK for {paper['title'][:40]}"
