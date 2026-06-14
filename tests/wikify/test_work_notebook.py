"""Tests for ``wikify.bundle.work.notebook`` — researcher notebook frontmatter."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.notebook import (
    CoveredDoc,
    ExplorationLogEntry,
    Notebook,
    NotebookFront,
    RoundHistoryEntry,
    append_exploration_log,
    append_round_history,
    init_notebook,
    list_notebook_slugs,
    merge_covered_chunks,
    merge_covered_docs,
    notebook_path,
    read_notebook,
    save_notebook,
    set_new_doc_action_needed,
)
from wikify.cli import app

runner = CliRunner()


def _bundle(tmp_path: Path) -> Bundle:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "run").mkdir()
    b = Bundle(root=root)
    init_run(b, corpus_path="data/corpora/foo")
    return b


def test_init_notebook_creates_skeleton(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    n = init_notebook(bundle, slug="memristor", kind="article")
    assert n.front.slug == "memristor"
    assert n.front.kind == "article"
    assert n.front.maturity.kind_stencil == "article-method"
    assert "Working summary" in n.body
    assert notebook_path(bundle, "memristor").exists()


def test_init_notebook_is_idempotent(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    init_notebook(bundle, slug="ald", kind="article", seed_docs=["doc:abc"])
    # Mutate on disk to detect overwrite.
    nb = read_notebook(bundle, "ald")
    nb.front.maturity.score = 0.42
    save_notebook(bundle, "ald", nb)
    init_notebook(bundle, slug="ald", kind="article")
    again = read_notebook(bundle, "ald")
    assert again.front.maturity.score == 0.42
    assert again.front.provenance.seed_docs == ["doc:abc"]


def test_notebook_round_trip_preserves_provenance(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    nb = Notebook(
        front=NotebookFront(
            slug="x",
            kind="article",
            provenance={
                "seed_docs": ["doc:abc"],
                "covered_docs": [
                    {"doc_id": "doc:abc", "n_chunks": 4,
                     "first_round": 1, "last_round": 3},
                ],
                "covered_chunks": ["chunk:abc__c0001", "chunk:abc__c0002"],
            },
            exploration_log=[
                ExplorationLogEntry(round=1, pattern="P1", target="doc:abc",
                                    depth=2, accepted=6),
            ],
            round_history=[RoundHistoryEntry(round=1, score=0.31,
                                             appended_chunks=6)],
        ),
        body="working notes\n",
    )
    save_notebook(bundle, "x", nb)
    loaded = read_notebook(bundle, "x")
    assert loaded.front.provenance.covered_chunks == [
        "chunk:abc__c0001", "chunk:abc__c0002"
    ]
    assert loaded.front.exploration_log[0].pattern == "P1"
    assert loaded.front.round_history[0].score == 0.31
    assert loaded.body.startswith("working notes")


def test_merge_covered_docs_increments_existing(tmp_path: Path) -> None:
    existing = [CoveredDoc(doc_id="doc:a", n_chunks=2,
                            first_round=1, last_round=1)]
    merged = merge_covered_docs(
        existing, additions={"doc:a": 3, "doc:b": 1}, round_=2
    )
    by_doc = {d.doc_id: d for d in merged}
    assert by_doc["doc:a"].n_chunks == 5
    assert by_doc["doc:a"].last_round == 2
    assert by_doc["doc:b"].n_chunks == 1
    assert by_doc["doc:b"].first_round == 2


def test_append_exploration_log_caps_at_max() -> None:
    log: list[ExplorationLogEntry] = []
    for i in range(12):
        log = append_exploration_log(
            log, ExplorationLogEntry(round=i, pattern="P1")
        )
    assert len(log) == 8
    assert log[0].round == 4
    assert log[-1].round == 11


def test_append_round_history_caps_at_max() -> None:
    history: list[RoundHistoryEntry] = []
    for i in range(8):
        history = append_round_history(
            history, RoundHistoryEntry(round=i)
        )
    assert len(history) == 5
    assert history[0].round == 3
    assert history[-1].round == 7


def test_merge_covered_chunks_dedupes_preserving_order() -> None:
    existing = ["c1", "c2"]
    merged = merge_covered_chunks(existing, ["c2", "c3", "c1", "c4"])
    assert merged == ["c1", "c2", "c3", "c4"]


def test_set_new_doc_action_needed_materialises_skeleton(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    nb = set_new_doc_action_needed(bundle, "no-existing", True)
    assert nb.front.new_doc_action_needed is True
    again = read_notebook(bundle, "no-existing")
    assert again.front.new_doc_action_needed is True


def test_set_new_doc_action_needed_flips_existing(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    init_notebook(bundle, slug="alpha", kind="article")
    set_new_doc_action_needed(bundle, "alpha", True)
    assert read_notebook(bundle, "alpha").front.new_doc_action_needed is True
    set_new_doc_action_needed(bundle, "alpha", False)
    assert read_notebook(bundle, "alpha").front.new_doc_action_needed is False


def test_list_notebook_slugs(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    init_notebook(bundle, slug="alpha", kind="article")
    init_notebook(bundle, slug="beta", kind="article")
    # A slug folder without notebook.md should not appear.
    (bundle.work_concepts_dir / "ghost").mkdir(parents=True, exist_ok=True)
    assert list_notebook_slugs(bundle) == ["alpha", "beta"]


def test_cli_notebook_init_creates_file(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    # Create the concept first so notebook-init can derive kind.
    res = runner.invoke(
        app,
        [
            "work", "add", "concept", "Atomic Layer Deposition",
            "--run", str(bundle.root),
        ],
    )
    assert res.exit_code == 0, res.output

    res = runner.invoke(
        app,
        [
            "work", "notebook-init", "atomic-layer-deposition",
            "--run", str(bundle.root),
            "--seed-docs", json.dumps(["doc:abc"]),
            "--format", "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["slug"] == "atomic-layer-deposition"
    nb = read_notebook(bundle, "atomic-layer-deposition")
    assert nb.front.provenance.seed_docs == ["doc:abc"]
