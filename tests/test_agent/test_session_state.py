from __future__ import annotations

from types import SimpleNamespace

from wikify.papers.agent.concept_graph import get_concept_graph
from wikify.papers.agent.reading_log import configure_reading_log, get_reading_log, reset_reading_log
from wikify.papers.agent.run_context import create_run_context, use_run_context
from wikify.papers.agent.scripted import scripted_explore
from wikify.papers.agent.tools import get_paper_summaries, record_paper_summary
from wikify.papers.agent.workflows import explore_corpus


def test_reset_reading_log_can_use_custom_backing_file(tmp_path):
    log_path = tmp_path / "run-a" / "reading.jsonl"
    configure_reading_log(log_path)

    log = reset_reading_log()
    assert log.entries == []

    get_reading_log().log("Paper A", "deep_read", "seed paper", depth="full")
    assert log_path.exists()
    assert "Paper A" in log_path.read_text(encoding="utf-8")

    reset_reading_log(log_path)
    assert not log_path.exists()


def test_run_context_isolates_logs_summaries_and_concept_graph(tmp_path):
    ctx_a = create_run_context(
        topic="topic a",
        strategy="explore",
        log_file=tmp_path / "run-a" / "reading.jsonl",
    )
    ctx_b = create_run_context(
        topic="topic b",
        strategy="explore",
        log_file=tmp_path / "run-b" / "reading.jsonl",
    )

    with use_run_context(ctx_a):
        reset_reading_log()
        get_reading_log().log("Paper A", "read_paper_digest", "seed", depth="digest")
        record_paper_summary("Paper A", ["finding"], ["42"], "relevant")
        get_concept_graph().add_edge("material", "device", "enables", "Paper A")

    with use_run_context(ctx_b):
        reset_reading_log()
        assert get_reading_log().entries == []
        assert get_paper_summaries() == []
        assert get_concept_graph().edges == []

    with use_run_context(ctx_a):
        assert {entry.paper for entry in get_reading_log().entries} == {"Paper A"}
        assert {entry.depth for entry in get_reading_log().entries} == {"digest", "full"}
        assert get_paper_summaries()[0]["paper_name"] == "Paper A"
        assert len(get_concept_graph().edges) == 1


def test_scripted_explore_resets_concept_graph(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "wikify.papers.agent.concept_graph.reset_concept_graph",
        lambda: calls.append("reset_concept_graph"),
    )
    monkeypatch.setattr(
        "wikify.papers.agent.reading_log.reset_reading_log",
        lambda *args, **kwargs: calls.append("reset_reading_log"),
    )
    monkeypatch.setattr(
        "wikify.papers.agent.tools.reset_paper_summaries",
        lambda: calls.append("reset_paper_summaries"),
    )
    monkeypatch.setattr(
        "wikify.papers.evaluate.frontier.frontier_exploration_order",
        lambda max_papers=12: [],
    )
    monkeypatch.setattr("wikify.papers.agent.tools.find_corpus_gaps", lambda: "")
    monkeypatch.setattr("wikify.papers.agent.tools.find_synthesis_opportunities", lambda: "")
    monkeypatch.setattr("wikify.papers.agent.tools.deep_read", lambda *args, **kwargs: "")
    monkeypatch.setattr("wikify.papers.agent.tools.read_paper_digest", lambda *args, **kwargs: "")

    class _ExecResult:
        def all(self):
            return []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def exec(self, _query):
            return _ExecResult()

    monkeypatch.setattr("wikify.core.store.db.get_session", lambda: _Session())

    result = scripted_explore(max_papers=1, n_deep=0)

    assert result["papers"] == []
    assert "reset_concept_graph" in calls


def test_explore_corpus_resets_concept_graph(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "wikify.papers.agent.concept_graph.reset_concept_graph",
        lambda: calls.append("reset_concept_graph"),
    )
    monkeypatch.setattr(
        "wikify.papers.agent.reading_log.reset_reading_log",
        lambda *args, **kwargs: calls.append("reset_reading_log"),
    )
    monkeypatch.setattr(
        "wikify.papers.agent.tools.reset_paper_summaries",
        lambda: calls.append("reset_paper_summaries"),
    )
    monkeypatch.setattr("wikify.papers.agent.defaults.build_explorer_prompt", lambda prompt: prompt)
    monkeypatch.setattr("wikify.papers.agent.defaults.get_explorer_tools", lambda: [])

    class _FakeNotes:
        gap_analysis = ""
        proposed_outline: list[str] = []

    monkeypatch.setattr(
        "wikify.papers.agent.research_notes.ResearchNotes",
        SimpleNamespace(from_session=lambda topic, run_context=None: _FakeNotes()),
    )

    class _FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, prompt, max_turns=25):
            return SimpleNamespace(content="")

    monkeypatch.setattr("wikify.papers.agent.workflows.ScholarForgeAgent", _FakeAgent)

    explore_corpus("test topic")

    assert "reset_concept_graph" in calls
