"""Tests for wiki telemetry and visible/operational run artifacts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wikify.store.db import DatabaseManager
from wikify.store.models import GraphEdge
from wikify.wiki.builder import article_path, write_article
from wikify.wiki.observability import begin_run, finish_run, snapshot_wiki_metrics


def _session_factory(tmp_path: Path):
    db = DatabaseManager(db_path=str(tmp_path / "test.db"))

    def _new_session():
        return db.session()

    return _new_session


def test_snapshot_wiki_metrics_writes_metric_export(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"

    article = article_path(wiki_dir, "concepts", "atomic_layer_deposition")
    write_article(
        path=article,
        title="Atomic Layer Deposition",
        content="ALD is related to [[Hafnium Oxide]].",
        sources=["paper-1"],
        topics=["method"],
        status="full",
        model="test-model",
        page_type="concept",
        domains=["materials"],
    )

    with patch("wikify.wiki.observability.runs.get_session", side_effect=session_factory):
        run_id = begin_run(workflow_type="epoch")
        with session_factory() as session:
            session.add(
                GraphEdge(
                    source_slug="atomic_layer_deposition",
                    target_slug="hafnium_oxide",
                    relation_type="related",
                    weight=1.0,
                    epoch=1,
                )
            )
            session.commit()

        metrics = snapshot_wiki_metrics(wiki_dir, run_id)

    assert metrics["article_count"] == 1.0
    assert metrics["link_count"] == 1.0
    assert (wiki_dir / "_meta" / "metrics" / f"{run_id}.json").exists()


def test_finish_run_writes_log_and_run_summary(tmp_path: Path):
    session_factory = _session_factory(tmp_path)
    wiki_dir = tmp_path / "wiki"

    with patch("wikify.wiki.observability.runs.get_session", side_effect=session_factory):
        run_id = begin_run(workflow_type="epoch")
        finish_run(
            wiki_dir,
            run_id,
            status="applied",
            headline="Epoch 1",
            summary={
                "workflow_type": "epoch",
                "epoch": 1,
                "articles_written": 2,
                "loss": 0.21,
            },
        )

    assert (wiki_dir / "_meta" / "runs" / f"{run_id}.json").exists()
    log_text = (wiki_dir / "log.md").read_text(encoding="utf-8")
    assert "Epoch 1" in log_text
    assert "articles_written: 2" in log_text
