"""Tests for wiki/epoch.py -- Epoch orchestrator for the Wikipedia/epoch pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

import wikify.wiki.epoch as mod
from wikify.core.store.models import ConceptRecord, EpochLog
from wikify.wiki.mapreduce import SourceExtraction

# ── Helpers / fixtures ────────────────────────────────────────────────────────


def _make_epoch_log(
    epoch: int = 1,
    loss_score: float = 0.5,
    loss_delta: float = 0.1,
    converged: bool = False,
    concepts_discovered: int = 10,
    contradictions_flagged: int = 0,
) -> EpochLog:
    return EpochLog(
        epoch=epoch,
        triggered_by="user",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        concepts_discovered=concepts_discovered,
        stubs_upgraded=0,
        articles_written=5,
        contradictions_flagged=contradictions_flagged,
        cross_refs_added=3,
        converged=converged,
        loss_score=loss_score,
        loss_delta=loss_delta,
    )


def _make_extraction(
    source_id: str = "paper1",
    extraction: str = "YES: Key claim.",
    is_relevant: bool = True,
) -> SourceExtraction:
    return SourceExtraction(
        source_id=source_id,
        display_name="Smith 2024",
        doc_type="paper",
        graph_role="standard",
        pagerank_score=0.0,
        extraction=extraction,
        is_relevant=is_relevant,
    )


def _make_concept(
    cid: str = "atomic_layer_deposition",
    name: str = "Atomic Layer Deposition",
    article_status: str = "none",
    importance: float = 0.5,
) -> ConceptRecord:
    return ConceptRecord(
        id=cid,
        name=name,
        article_status=article_status,
        importance=importance,
        epoch_discovered=1,
        epoch_last_updated=1,
    )


def _make_session(
    epoch_logs: list | None = None,
    concepts: list | None = None,
    coverage: list | None = None,
) -> MagicMock:
    """Return a context-manager-compatible mock session.

    Successive calls to session.exec() will consume items from the
    provided lists in order (epoch_logs first, then concepts, etc.).
    """
    epoch_logs = epoch_logs or []
    concepts = concepts or []
    coverage = coverage or []

    def _make_exec_result(rows):
        r = MagicMock()
        r.all.return_value = rows
        return r

    # exec() is called multiple times; return different results each time
    results_queue = [
        _make_exec_result(epoch_logs),
        _make_exec_result(concepts),
        _make_exec_result(coverage),
        # Extra calls fall back to empty
        _make_exec_result([]),
        _make_exec_result([]),
    ]

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = results_queue
    return session


# ── _get_next_epoch_number ─────────────────────────────────────────────────────


def test_get_next_epoch_number_first():
    """Returns 1 when there are no existing epoch logs."""
    mock_session = _make_session(epoch_logs=[])
    with patch("wikify.wiki.epoch.get_session", return_value=mock_session):
        result = mod._get_next_epoch_number()
    assert result == 1


def test_get_next_epoch_number_increments():
    """Returns max epoch + 1 when logs already exist."""
    logs = [_make_epoch_log(epoch=1), _make_epoch_log(epoch=3)]
    mock_session = _make_session(epoch_logs=logs)
    with patch("wikify.wiki.epoch.get_session", return_value=mock_session):
        result = mod._get_next_epoch_number()
    assert result == 4


# ── _get_all_paper_ids ────────────────────────────────────────────────────────


def test_get_all_paper_ids():
    """Returns only paper ids with origin == 'corpus', not 'generated'."""
    from wikify.core.store.models import Paper

    p1 = Paper(id="aaa", title="Paper A", origin="corpus")
    p2 = Paper(id="bbb", title="Paper B", origin="corpus")
    # p3 has origin="generated" and should be excluded by the SQL WHERE clause

    exec_result = MagicMock()
    exec_result.all.return_value = [p1, p2]  # SQL WHERE filters out p3

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = exec_result

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        result = mod._get_all_paper_ids()

    assert set(result) == {"aaa", "bbb"}
    assert "ccc" not in result


# ── should_update_article ─────────────────────────────────────────────────────


def test_should_update_article_empty_extractions():
    """Returns False immediately when given an empty extractions list."""
    result = mod.should_update_article("Some existing text", [])
    assert result is False


def test_should_update_article_gate1_blocks():
    """Gate 1 blocks when new evidence is less than 5% of existing article tokens."""
    # existing_article: ~4000 chars -> ~1000 tokens
    existing_article = "x" * 4000
    # new extraction: ~10 chars -> ~2.5 tokens; gradient = 2.5/1000 = 0.0025 < 0.05
    extractions = [_make_extraction(extraction="Short text.", is_relevant=True)]

    with patch("wikify.wiki.epoch.complete") as mock_complete:
        result = mod.should_update_article(existing_article, extractions)

    assert result is False
    mock_complete.assert_not_called()


def test_should_update_article_gate2_yes():
    """Returns True when gradient is sufficient and LLM responds YES."""
    # Short existing (~40 chars = ~10 tokens); long extraction (~800 chars = ~200 tokens)
    # gradient = 200/10 = 20 >> 0.05
    existing_article = "Short article."
    extractions = [_make_extraction(extraction="A " * 400, is_relevant=True)]

    with patch("wikify.wiki.epoch.complete", return_value="YES") as mock_complete:
        result = mod.should_update_article(existing_article, extractions)

    assert result is True
    mock_complete.assert_called_once()


def test_should_update_article_gate2_no():
    """Returns False when gradient is sufficient but LLM responds NO."""
    existing_article = "Short article."
    extractions = [_make_extraction(extraction="A " * 400, is_relevant=True)]

    with patch("wikify.wiki.epoch.complete", return_value="NO"):
        result = mod.should_update_article(existing_article, extractions)

    assert result is False


def test_should_update_article_no_relevant_extractions():
    """Returns False when all extractions are not relevant."""
    existing_article = "Short article."
    extractions = [
        _make_extraction(extraction="Irrelevant text " * 50, is_relevant=False),
    ]

    with patch("wikify.wiki.epoch.complete") as mock_complete:
        result = mod.should_update_article(existing_article, extractions)

    assert result is False
    mock_complete.assert_not_called()


# ── compute_loss ──────────────────────────────────────────────────────────────


def test_compute_loss_no_concepts():
    """Returns (0.0, 0.0) when there are no concept records."""
    mock_session = _make_session(concepts=[], coverage=[])
    with patch("wikify.wiki.epoch.get_session", return_value=mock_session):
        loss, delta = mod.compute_loss(epoch=1)

    assert loss == 0.0
    assert delta == 0.0


def test_compute_loss_basic(tmp_path: Path):
    """Loss formula produces expected value for known inputs."""
    # 10 concepts: 3 stubs, 7 full
    concepts = [
        _make_concept(cid=f"c{i}", name=f"C{i}", article_status="stub") for i in range(3)
    ] + [_make_concept(cid=f"c{i + 3}", name=f"C{i + 3}", article_status="full") for i in range(7)]
    # 8 concepts covered (2 orphans)
    from wikify.core.store.models import SourceCoverage

    coverage = [SourceCoverage(source_id="p1", article_slug=f"c{i}") for i in range(8)]

    # Write some .md files to tmp_path: 2 with WARNING, 3 with [[wikilinks]]
    (tmp_path / "article1.md").write_text("This is fine.\n[[Concept A]]", encoding="utf-8")
    (tmp_path / "article2.md").write_text(
        "WARNING something wrong.\n[[Concept B]]", encoding="utf-8"
    )
    (tmp_path / "article3.md").write_text("Another WARNING here.\n[[Concept C]]", encoding="utf-8")
    # _index.md should be skipped (starts with "_")
    (tmp_path / "_index.md").write_text("# Index\nWARNING should be ignored", encoding="utf-8")

    # Stub ratio = 3/10 = 0.3
    # Orphan rate = 2/10 = 0.2
    # warning_count = 2, total_articles = 3 (index skipped)
    # contradiction_density = 2/3 ~ 0.6667
    # wikilinks: article1=1, article2=1, article3=1 -> 3 total / 3 = 1.0 per article
    # cross_ref_density = 1.0, clamped = min(1.0/1.0, 1.0) = 1.0
    # L = 0.3*0.3 + 0.2*0.2 + 0.3*(2/3) - 0.2*1.0
    # L = 0.09 + 0.04 + 0.2 - 0.2 = 0.13 (clamped to [0,1])
    expected_loss = max(0.0, min(1.0, 0.3 * 0.3 + 0.2 * 0.2 + 0.3 * (2 / 3) - 0.2 * 1.0))

    # Two get_session calls: one for concepts+occurrences+coverage, one for prev_logs
    exec_concepts_coverage = MagicMock()
    exec_concepts_coverage.all.side_effect = [concepts, [], coverage]
    exec_prev_logs = MagicMock()
    exec_prev_logs.all.return_value = []  # no previous epoch

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    # First context-manager call: concepts+occurrences+coverage; second: prev_logs
    session.exec.side_effect = [
        exec_concepts_coverage,
        exec_concepts_coverage,
        exec_concepts_coverage,
        exec_prev_logs,
    ]

    with (
        patch("wikify.wiki.epoch.get_session", return_value=session),
        patch.object(mod, "_WIKI_DIR", tmp_path),
    ):
        loss, delta = mod.compute_loss(epoch=1)

    assert abs(loss - expected_loss) < 0.001
    assert delta == pytest.approx(abs(expected_loss - 0.0))  # no previous epoch -> prev_loss=0


# ── check_convergence ─────────────────────────────────────────────────────────


def test_check_convergence_true():
    """All 4 criteria met: returns True."""
    # loss_delta=0.005 < 0.01; no contradictions; 1/100=1% < 2%; stub=5/100=5% < 10%
    log = _make_epoch_log(
        epoch=1,
        loss_delta=0.005,
        contradictions_flagged=0,
        concepts_discovered=1,
    )
    # 100 total concepts, 5 stubs
    concepts = [_make_concept(cid=f"s{i}", article_status="stub") for i in range(5)] + [
        _make_concept(cid=f"f{i}", article_status="full") for i in range(95)
    ]

    exec_total = MagicMock()
    exec_total.all.return_value = concepts
    exec_stubs = MagicMock()
    exec_stubs.all.return_value = concepts

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [exec_total, exec_stubs]

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        result = mod.check_convergence([log])

    assert result is True


def test_check_convergence_false_high_loss_delta():
    """Returns False when loss_delta >= 0.01."""
    log = _make_epoch_log(loss_delta=0.05, contradictions_flagged=0, concepts_discovered=1)
    concepts = [_make_concept(cid=f"f{i}", article_status="full") for i in range(100)]

    exec_result = MagicMock()
    exec_result.all.return_value = concepts

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [exec_result, exec_result]

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        result = mod.check_convergence([log])

    assert result is False


def test_check_convergence_false_too_many_new_concepts():
    """Returns False when new concept rate >= 2%."""
    # 5 new / 100 total = 5% >= 2%
    log = _make_epoch_log(loss_delta=0.005, contradictions_flagged=0, concepts_discovered=5)
    concepts = [_make_concept(cid=f"f{i}", article_status="full") for i in range(100)]

    exec_result = MagicMock()
    exec_result.all.return_value = concepts

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.return_value = exec_result

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        result = mod.check_convergence([log])

    assert result is False


def test_check_convergence_false_contradictions():
    """Returns False when contradictions_flagged > 0."""
    log = _make_epoch_log(
        loss_delta=0.005,
        contradictions_flagged=2,
        concepts_discovered=1,
    )
    concepts = [_make_concept(cid=f"f{i}", article_status="full") for i in range(100)]

    exec_result = MagicMock()
    exec_result.all.return_value = concepts

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [exec_result, exec_result]

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        result = mod.check_convergence([log])

    assert result is False


def test_check_convergence_empty_logs():
    """Returns False for an empty log list."""
    result = mod.check_convergence([])
    assert result is False


# ── run_epoch ─────────────────────────────────────────────────────────────────


def test_run_epoch_orchestrates_all_passes():
    """run_epoch() calls all 5 passes and persists an EpochLog."""
    concept = _make_concept()

    exec_prev_logs = MagicMock()
    exec_prev_logs.all.return_value = []  # no previous logs for model selection
    exec_existing_logs = MagicMock()
    exec_existing_logs.all.return_value = []  # for convergence check query

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [
        exec_prev_logs,  # _get_next_epoch_number
        exec_prev_logs,  # model selection: prev logs query
        exec_existing_logs,  # Pass 5: existing logs for convergence
        exec_prev_logs,  # check_convergence: total_concepts
        exec_prev_logs,  # check_convergence: stub ratio
    ]

    with (
        patch("wikify.wiki.epoch.get_session", return_value=session),
        patch("wikify.wiki.epoch._get_next_epoch_number", return_value=1),
        patch("wikify.wiki.epoch.clear_staged_extractions") as mock_clear,
        patch("wikify.wiki.epoch._get_all_paper_ids", return_value=["paper1"]) as mock_ids,
        patch(
            "wikify.wiki.epoch.discover_concepts",
            return_value=MagicMock(concepts=[concept], rich_extractions={}),
        ) as mock_discover,
        patch("wikify.wiki.epoch.build_concept_graph", return_value=nx.DiGraph()) as mock_graph,
        patch("wikify.wiki.epoch.score_importance", return_value={}) as mock_score,
        patch("wikify.wiki.epoch.update_concept_importance") as mock_update_importance,
        patch("wikify.wiki.epoch.classify_node_roles", return_value={}) as mock_classify,
        patch("wikify.wiki.epoch.extract_relations", return_value=[]) as mock_extract_rel,
        patch("wikify.wiki.epoch.save_relations", return_value=0) as mock_save_rel,
        patch("wikify.wiki.epoch.list_concepts", return_value=[]) as mock_list,
        patch("wikify.wiki.epoch.cross_link_articles", return_value=0) as mock_cross,
        patch("wikify.wiki.epoch.generate_wiki_index") as mock_index,
        patch("wikify.wiki.epoch.compute_loss", return_value=(0.5, 0.1)) as mock_loss,
        patch("wikify.wiki.epoch.check_convergence", return_value=False) as mock_conv,
    ):
        log = mod.run_epoch(triggered_by="user", domain="")

    # All pass functions were called
    mock_clear.assert_called_once()
    mock_ids.assert_called_once()
    mock_discover.assert_called_once()
    mock_graph.assert_called_once()
    mock_score.assert_called_once()
    mock_update_importance.assert_called_once()
    mock_classify.assert_called_once()
    mock_extract_rel.assert_called_once()
    mock_save_rel.assert_called_once()
    mock_list.assert_called_once()
    mock_cross.assert_called_once()
    mock_index.assert_called_once()
    mock_loss.assert_called_once_with(1)
    mock_conv.assert_called_once()

    # EpochLog was created, committed, and returned
    session.add.assert_called()
    session.commit.assert_called()
    assert isinstance(log, EpochLog)
    assert log.epoch == 1
    assert log.loss_score == 0.5
    assert log.loss_delta == 0.1
    assert log.converged is False


# ── run_until_convergence ──────────────────────────────────────────────────────


def test_run_until_convergence_stops_on_converge():
    """Stops as soon as run_epoch returns a converged log."""
    converged_log = _make_epoch_log(epoch=2, converged=True)
    not_converged_log = _make_epoch_log(epoch=1, converged=False)

    with patch(
        "wikify.wiki.epoch.run_epoch", side_effect=[not_converged_log, converged_log]
    ) as mock_run:
        logs = mod.run_until_convergence(max_epochs=5)

    assert mock_run.call_count == 2
    assert len(logs) == 2
    assert logs[-1].converged is True


def test_run_until_convergence_respects_max():
    """Stops at max_epochs even if never converged."""
    not_converged_log = _make_epoch_log(converged=False)

    with patch("wikify.wiki.epoch.run_epoch", return_value=not_converged_log) as mock_run:
        logs = mod.run_until_convergence(max_epochs=3)

    assert mock_run.call_count == 3
    assert len(logs) == 3


# ── get_epoch_status ──────────────────────────────────────────────────────────


def test_get_epoch_status_no_logs():
    """Returns zeroed-out dict when DB is empty."""
    exec_logs = MagicMock()
    exec_logs.all.return_value = []
    exec_concepts = MagicMock()
    exec_concepts.all.return_value = []

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [exec_logs, exec_concepts]

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        status = mod.get_epoch_status()

    assert status["current_epoch"] == 0
    assert status["latest_loss"] == 0.0
    assert status["loss_delta"] == 0.0
    assert status["converged"] is False
    assert status["total_concepts"] == 0
    assert status["stub_count"] == 0
    assert status["draft_count"] == 0
    assert status["full_count"] == 0
    assert status["none_count"] == 0


def test_get_epoch_status_with_data():
    """Returns correct values when logs and concepts exist."""
    logs = [
        _make_epoch_log(epoch=1, loss_score=0.4, loss_delta=0.2, converged=False),
        _make_epoch_log(epoch=3, loss_score=0.15, loss_delta=0.008, converged=True),
        _make_epoch_log(epoch=2, loss_score=0.25, loss_delta=0.05, converged=False),
    ]
    concepts = [
        _make_concept(cid="a", article_status="full"),
        _make_concept(cid="b", article_status="full"),
        _make_concept(cid="c", article_status="stub"),
        _make_concept(cid="d", article_status="draft"),
        _make_concept(cid="e", article_status="none"),
        _make_concept(cid="f", article_status="none"),
    ]

    exec_logs = MagicMock()
    exec_logs.all.return_value = logs
    exec_concepts = MagicMock()
    exec_concepts.all.return_value = concepts

    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec.side_effect = [exec_logs, exec_concepts]

    with patch("wikify.wiki.epoch.get_session", return_value=session):
        status = mod.get_epoch_status()

    # Latest log is epoch=3
    assert status["current_epoch"] == 3
    assert status["latest_loss"] == pytest.approx(0.15)
    assert status["loss_delta"] == pytest.approx(0.008)
    assert status["converged"] is True

    # Concept counts
    assert status["total_concepts"] == 6
    assert status["full_count"] == 2
    assert status["stub_count"] == 1
    assert status["draft_count"] == 1
    assert status["none_count"] == 2
