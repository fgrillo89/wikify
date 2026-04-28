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


def test_corpus_show_doc_short_handle(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    # Test fixtures use plain ids (no hash); short handle == full id.
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:paper_0", "--corpus", str(corpus.root)],
    )
    assert result.exit_code == 0


def test_corpus_show_doc_unique_suffix(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c", n_docs=3)
    # `_2` only matches paper_2 (suffix-match tier).
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:_2", "--corpus", str(corpus.root)],
    )
    assert result.exit_code == 0
    assert "Title 2" in result.output


def test_corpus_find_quiet_emits_handles(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "2",
            "--format", "quiet",
        ],
    )
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines, result.output
    for line in lines:
        assert line.startswith("chunk:"), line


def test_corpus_find_compact_includes_cites_column(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "1",
            "--format", "compact",
        ],
    )
    assert result.exit_code == 0
    assert "cites=" in result.output
    # Tab-separated, handle column starts with 'chunk:'.
    line = next(line for line in result.output.splitlines() if "chunk:" in line)
    cols = line.split("\t")
    assert any(col.startswith("chunk:") for col in cols)


def test_corpus_find_quiet_pipes_into_show(tmp_path: Path) -> None:
    """Round-trip: every handle from `find --format quiet` resolves via `show`."""
    corpus = _make_corpus(tmp_path / "c")
    find_result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "4",
            "--format", "quiet",
        ],
    )
    assert find_result.exit_code == 0
    handles = [line.strip() for line in find_result.output.splitlines() if line.strip()]
    assert handles
    for handle in handles:
        show_result = runner.invoke(
            app,
            ["corpus", "show", handle, "--corpus", str(corpus.root)],
        )
        assert show_result.exit_code == 0, (handle, show_result.output)


def test_corpus_traverse_chunk_to_source(tmp_path: Path) -> None:
    """Chunk -> source traversal returns the parent doc handle."""
    corpus = _make_corpus(tmp_path / "c")
    # Build the graph so traverse has something to read.
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)

    result = runner.invoke(
        app,
        [
            "corpus", "traverse", "chunk:paper_0__c0000",
            "--corpus", str(corpus.root),
            "--to", "source",
            "--format", "quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines == ["doc:paper_0"]


def test_corpus_traverse_unknown_relation_errors(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)
    result = runner.invoke(
        app,
        [
            "corpus", "traverse", "doc:paper_0",
            "--corpus", str(corpus.root),
            "--to", "bogus",
        ],
    )
    assert result.exit_code != 0


def test_corpus_resolves_via_wikify_corpus_env(
    tmp_path: Path, monkeypatch
) -> None:
    """When --corpus is omitted, fall back to WIKIFY_CORPUS env var."""
    corpus = _make_corpus(tmp_path / "c")
    monkeypatch.setenv("WIKIFY_CORPUS", str(corpus.root))
    result = runner.invoke(app, ["corpus", "check"])
    assert result.exit_code == 0, result.output
    assert "docs:" in result.output


def test_corpus_resolves_via_cwd(tmp_path: Path, monkeypatch) -> None:
    """When --corpus and env are absent, walk up from cwd."""
    corpus = _make_corpus(tmp_path / "c")
    monkeypatch.delenv("WIKIFY_CORPUS", raising=False)
    monkeypatch.chdir(corpus.root)
    result = runner.invoke(app, ["corpus", "check"])
    assert result.exit_code == 0, result.output
    assert "docs:" in result.output


def test_corpus_no_corpus_resolved_errors(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing flag, env, and cwd context all produce a clear error."""
    monkeypatch.delenv("WIKIFY_CORPUS", raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["corpus", "list", "docs"])
    assert result.exit_code != 0


def test_corpus_schema_text(tmp_path: Path) -> None:
    """`corpus schema` is self-describing and runs without a corpus."""
    result = runner.invoke(app, ["corpus", "schema"])
    assert result.exit_code == 0
    assert "Node types:" in result.output
    assert "cited-by" in result.output
    assert "h_index" in result.output


def test_corpus_schema_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["corpus", "schema", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "node_types" in data
    assert "traverse_relations" in data
    assert "doc" in data["traverse_relations"]


def test_corpus_find_explain(tmp_path: Path) -> None:
    """--explain prints the chain without executing."""
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "2",
            "--explain",
        ],
    )
    assert result.exit_code == 0
    assert "chain:" in result.output
    assert "atomic layer" in result.output


def test_corpus_find_paper_by_citation_count(tmp_path: Path) -> None:
    """`--by paper --rank citation_count` returns docs ranked by metric."""
    corpus = _make_corpus(tmp_path / "c")
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--by", "paper",
            "--rank", "citation_count",
            "--top-k", "2",
            "--format", "quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    handles = [line.strip() for line in result.output.splitlines() if line.strip()]
    assert handles
    for h in handles:
        assert h.startswith("doc:"), h


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
