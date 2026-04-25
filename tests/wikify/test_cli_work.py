"""Tests for `wikify work ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.cli import app

runner = CliRunner()


def _init_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    runner.invoke(
        app, ["run", "init", "--bundle", str(bundle), "--corpus", str(corpus)]
    )
    return bundle


def test_work_add_concept(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app,
        [
            "work", "add", "concept",
            "Atomic Layer Deposition",
            "--run", str(bundle),
            "--kind", "article",
            "--aliases", '["ALD"]',
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["slug"] == "atomic-layer-deposition"


def test_work_list_concepts(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(
        app, ["work", "add", "concept", "ALD", "--run", str(bundle)]
    )
    runner.invoke(
        app, ["work", "add", "concept", "CVD", "--run", str(bundle)]
    )
    result = runner.invoke(app, ["work", "list", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "ald" in result.output
    assert "cvd" in result.output


def test_work_show(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(app, ["work", "show", "ald", "--run", str(bundle)])
    assert result.exit_code == 0
    assert "ALD" in result.output


def test_work_show_unknown_concept(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    result = runner.invoke(
        app, ["work", "show", "no-such", "--run", str(bundle)]
    )
    assert result.exit_code != 0


def test_work_claim_release_roundtrip(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    claim = runner.invoke(
        app,
        ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"],
    )
    assert claim.exit_code == 0
    assert "claimed ald" in claim.output

    release = runner.invoke(
        app,
        ["work", "release", "ald", "--run", str(bundle), "--owner", "a"],
    )
    assert release.exit_code == 0


def test_work_claim_contention_exits_2(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "b"]
    )
    assert result.exit_code == 2


def test_work_release_non_owner_exits_2(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "release", "ald", "--run", str(bundle), "--owner", "b"]
    )
    assert result.exit_code == 2


def test_work_list_claims(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    runner.invoke(
        app, ["work", "claim", "ald", "--run", str(bundle), "--owner", "a"]
    )
    result = runner.invoke(
        app, ["work", "list", "claims", "--run", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0
    items = json.loads(result.output)["items"]
    assert len(items) == 1
    assert items[0]["slug"] == "ald"


def test_work_add_evidence_from_records(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    records = tmp_path / "ev.jsonl"
    records.write_text(
        '{"chunk_id": "d1:001", "doc_id": "d1", "score": 0.9}\n'
        '{"chunk_id": "d1:002", "doc_id": "d1", "score": 0.7}\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle),
            "--records", str(records),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["appended"] == 2


def test_work_set_status(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app,
        ["work", "set", "ald", "--run", str(bundle), "--status", "needs_refine"],
    )
    assert result.exit_code == 0
    show = runner.invoke(
        app, ["work", "show", "ald", "--run", str(bundle), "--format", "json"]
    )
    assert json.loads(show.output)["front"]["status"] == "needs_refine"


def test_work_tend_runs(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    runner.invoke(app, ["work", "add", "concept", "ALD", "--run", str(bundle)])
    result = runner.invoke(
        app, ["work", "tend", "--run", str(bundle), "--format", "json"]
    )
    assert result.exit_code == 0
    summary = json.loads(result.output)
    assert summary["concepts"] == 1
    assert "index_path" in summary


def test_work_add_feedback(tmp_path: Path) -> None:
    bundle = _init_bundle(tmp_path)
    record = tmp_path / "fb.json"
    record.write_text('{"query": "How does ALD differ from CVD?"}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "work", "add", "feedback", "query",
            "--run", str(bundle),
            "--record", str(record),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["kind"] == "query_feedback"
    assert data["appended"] == 1
