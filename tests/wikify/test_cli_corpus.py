"""Tests for `wikify corpus ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

# Reuse the on-disk corpus builder from test_corpus_queries.
from tests.wikify.test_corpus_queries import _make_corpus  # type: ignore  # noqa: E402
from wikify.cli import app

runner = CliRunner()


def test_corpus_check_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(app, ["corpus", "check", str(corpus.root)])
    assert result.exit_code == 0
    assert "docs:" in result.output
    assert "chunks:" in result.output


def test_corpus_check_json(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app, ["corpus", "check", str(corpus.root), "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["n_docs"] == 2
    assert data["n_chunks"] == 4


def test_corpus_list_docs(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app, ["corpus", "list", "docs", "--corpus", str(corpus.root)]
    )
    assert result.exit_code == 0
    assert "paper_0" in result.output
    assert "paper_1" in result.output


def test_corpus_list_docs_json(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        ["corpus", "list", "docs", "--corpus", str(corpus.root), "--format", "json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["items"] == ["paper_0", "paper_1"]


def test_corpus_list_chunks(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "list", "chunks",
            "--corpus", str(corpus.root),
            "--doc", "paper_0",
        ],
    )
    assert result.exit_code == 0
    assert "paper_0__c0000" in result.output
    assert "paper_0__c0001" in result.output


def test_corpus_list_files(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app, ["corpus", "list", "files", "--corpus", str(corpus.root)]
    )
    assert result.exit_code == 0
    assert "manifest.json" in result.output


def test_corpus_find_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text",
        ],
    )
    assert result.exit_code == 0
    assert "paper_0__c0000" in result.output


def test_corpus_find_requires_query(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    # Empty query without --seed/--text mode is rejected.
    result = runner.invoke(
        app, ["corpus", "find", "--corpus", str(corpus.root)]
    )
    assert result.exit_code != 0


def test_corpus_show_doc(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:paper_0", "--corpus", str(corpus.root)],
    )
    assert result.exit_code == 0
    assert "paper_0" in result.output
    assert "Title 0" in result.output


def test_corpus_show_chunk_full(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "show", "chunk:paper_0__c0000",
            "--corpus", str(corpus.root),
            "--full",
        ],
    )
    assert result.exit_code == 0
    assert "atomic layer deposition" in result.output


def test_corpus_show_unknown_doc_errors(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:no_such", "--corpus", str(corpus.root)],
    )
    assert result.exit_code != 0


def test_corpus_show_bad_handle(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app, ["corpus", "show", "bogus", "--corpus", str(corpus.root)]
    )
    assert result.exit_code != 0


def test_corpus_repl_text_find_and_show(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "repl",
            "--corpus", str(corpus.root),
            "--prompt", "",
        ],
        input=(
            "list docs\n"
            "find --text --top-k 1 atomic layer\n"
            "show chunk:paper_0__c0000 --full\n"
            "exit\n"
        ),
    )
    assert result.exit_code == 0, result.output
    assert "ready corpus=" in result.output
    assert "paper_0" in result.output
    assert "paper_0__c0000" in result.output
    assert "atomic layer deposition" in result.output


def test_corpus_repl_find_papers_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "repl",
            "--corpus", str(corpus.root),
            "--prompt", "",
        ],
        input="find-papers --text top=2 atomic layer\nexit\n",
    )
    assert result.exit_code == 0, result.output
    assert "n=2" in result.output
    assert "paper_0" in result.output
    assert "best=paper_0__c0000" in result.output


def test_corpus_repl_reports_user_errors_and_continues(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "repl",
            "--corpus", str(corpus.root),
            "--prompt", "",
        ],
        input="bogus\nlist docs\nexit\n",
    )
    assert result.exit_code == 0
    assert "error: unknown command: bogus; type help" in result.stderr
    assert "paper_0" in result.output
