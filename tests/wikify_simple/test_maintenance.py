"""Tests for the maintenance verb (feature 3).

Exercises:
- load_query_log: reads entries from disk, skips corrupt files
- run_maintenance: dispatches actions for unanswered queries
- run_maintenance: deletes log entries when target page exists
- run_maintenance: keeps log entries when target page is missing
- run_maintenance: dry_run=True does not delete anything
- escalation_events trigger add_evidence action
- maintenance CLI verb exits 0 with --help
"""

import json
from pathlib import Path

from wikify_simple.contracts.schema import (
    EscalationEvent,
    MaintenanceAction,
)
from wikify_simple.distill.maintenance import (
    load_query_log,
    run_maintenance,
)
from wikify_simple.distill.query import persist_query_log
from wikify_simple.paths import BundlePaths, CorpusPaths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


# --- helpers -----------------------------------------------------------


def _write_log_entry(bundle: BundlePaths, question: str, pages_touched=None, escalate=False) -> str:
    from wikify_simple.contracts.schema import QueryAnswer
    answer = QueryAnswer(text="Test answer.", citations=[], chunks=[], follow_ups=[])
    ev = [EscalationEvent(reason="r", chunk_ids=["c1"])] if escalate else []
    return persist_query_log(
        bundle,
        question=question,
        answer=answer,
        pages_touched=pages_touched or [],
        escalation_events=ev,
    )


# --- load_query_log ----------------------------------------------------


def test_load_query_log_empty(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    entries = load_query_log(bundle)
    assert entries == []


def test_load_query_log_reads_entries(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    _write_log_entry(bundle, "Q1")
    _write_log_entry(bundle, "Q2")
    entries = load_query_log(bundle)
    assert len(entries) == 2
    questions = {e.question for e in entries}
    assert "Q1" in questions and "Q2" in questions


def test_load_query_log_skips_corrupt(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    _write_log_entry(bundle, "Good Q")
    # Write a corrupt file.
    bundle.query_log_dir.mkdir(parents=True, exist_ok=True)
    (bundle.query_log_dir / "bad.json").write_text("{not valid json", encoding="utf-8")
    entries = load_query_log(bundle)
    assert len(entries) == 1
    assert entries[0].question == "Good Q"


# --- run_maintenance ---------------------------------------------------


def test_run_maintenance_empty_log(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    report = run_maintenance(bundle, corpus)
    assert report.queries_scanned == 0
    assert report.actions_dispatched == 0


def test_run_maintenance_unanswered_dispatches_action(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    _write_log_entry(bundle, "What is a memristor?", pages_touched=["Memristor"])
    report = run_maintenance(bundle, corpus)
    assert report.queries_scanned == 1
    assert report.actions_dispatched == 1
    assert len(report.actions) == 1
    action = report.actions[0]
    assert isinstance(action, MaintenanceAction)
    assert action.action in ("extend_page", "create_page", "add_evidence", "merge_pages")


def _write_fake_index(bundle: BundlePaths, page_id: str, page_path: str) -> None:
    """Write a minimal _index.json compatible with WikiIndex.load."""
    index_data = {
        "version": 1,
        "entries": [
            {
                "id": page_id,
                "kind": "article",
                "title": page_id,
                "aliases": [],
                "path": page_path,
                "n_evidence": 1,
                "doc_ids": [],
                "links": [],
            }
        ],
    }
    (bundle.root / "_index.json").write_text(json.dumps(index_data), encoding="utf-8")


def test_run_maintenance_deletes_log_when_page_exists(tmp_path):
    """When the target page exists in the bundle, log entry is deleted."""
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    page_dir = bundle.articles_dir
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Memristor.md").write_text("# Memristor\n\nSome body.", encoding="utf-8")
    _write_fake_index(bundle, "Memristor", "articles/Memristor.md")
    entry_id = _write_log_entry(bundle, "What is a memristor?", pages_touched=["Memristor"])
    report = run_maintenance(bundle, corpus)
    # Either covered (deleted immediately) or action applied (deleted after).
    assert report.query_logs_deleted >= 1
    assert not (bundle.query_log_dir / f"{entry_id}.json").exists()


def test_run_maintenance_keeps_log_when_page_missing(tmp_path):
    """When the target page is not in the bundle, log entry is kept."""
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    entry_id = _write_log_entry(bundle, "Unknown topic query", pages_touched=["UnknownPage"])
    run_maintenance(bundle, corpus)
    # The log file must still exist because the page was not found.
    log_file = bundle.query_log_dir / f"{entry_id}.json"
    assert log_file.exists(), "log entry was deleted before page was updated"


def test_run_maintenance_dry_run_does_not_delete(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    page_dir = bundle.articles_dir
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "Memristor.md").write_text("# Memristor\n\nBody.", encoding="utf-8")
    _write_fake_index(bundle, "Memristor", "articles/Memristor.md")
    entry_id = _write_log_entry(bundle, "Memristor question", pages_touched=["Memristor"])
    report = run_maintenance(bundle, corpus, dry_run=True)
    # dry_run=True: no deletions.
    assert report.query_logs_deleted == 0
    assert (bundle.query_log_dir / f"{entry_id}.json").exists()


def test_run_maintenance_escalation_triggers_add_evidence(tmp_path):
    bundle = BundlePaths(root=tmp_path / "bundle")
    bundle.ensure()
    corpus = CorpusPaths(root=tmp_path / "corpus")
    _write_log_entry(bundle, "Complex question?", pages_touched=["P1"], escalate=True)
    report = run_maintenance(bundle, corpus)
    assert report.actions_dispatched == 1
    assert report.actions[0].action == "add_evidence"
    assert "c1" in report.actions[0].evidence_additions


# --- CLI --help ----------------------------------------------------------


def test_maintenance_cli_help():
    from typer.testing import CliRunner

    from wikify_simple.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["maintenance", "--help"])
    assert result.exit_code == 0
    assert "--bundle" in result.output
