"""Tests for `wikify corpus ...` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

# Reuse the on-disk corpus builder from test_corpus_queries.
from tests.wikify.test_corpus_queries import (
    _make_corpus,  # type: ignore  # noqa: E402
    _make_sqlite_only_corpus,  # type: ignore  # noqa: E402
)
from wikify.cli import app

runner = CliRunner()


def test_corpus_check_text(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(app, ["corpus", "check", str(corpus.root)])
    assert result.exit_code == 0
    assert "docs:" in result.output
    assert "chunks:" in result.output
    assert "sqlite:" in result.output
    assert "graph:" not in result.output


def test_corpus_check_json(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app, ["corpus", "check", str(corpus.root), "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["n_docs"] == 2
    assert data["n_chunks"] == 4
    assert data["has_sqlite_store"] is True
    assert "has_knowledge_graph" not in data


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
    # Default: short handles so the items are pipeable directly into
    # `corpus show` / `corpus traverse`.
    assert json.loads(result.output)["items"] == ["doc:paper_0", "doc:paper_1"]


def test_corpus_list_docs_json_long(tmp_path: Path) -> None:
    """`--long` recovers the legacy bare-internal-id JSON shape."""
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "list", "docs",
            "--corpus", str(corpus.root),
            "--format", "json", "--long",
        ],
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
    # `2` only matches paper_2 via the `_<short>` suffix tier — agents
    # pass the short fragment, not a leading underscore.
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:2", "--corpus", str(corpus.root)],
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


def test_corpus_find_chunk_with_metric_rank_errors(tmp_path: Path) -> None:
    """--by chunk silently ignored --rank citation_count; now rejects."""
    corpus = _make_corpus(tmp_path / "c")
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--by", "chunk", "--rank", "citation_count",
            "--top-k", "2",
        ],
    )
    assert result.exit_code != 0
    assert "bad_rank_by_combo" in (result.output + result.stderr)


def test_corpus_find_paper_with_h_index_rank_errors(tmp_path: Path) -> None:
    """h_index applies to authors, not papers."""
    corpus = _make_corpus(tmp_path / "c")
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--by", "paper", "--rank", "h_index",
        ],
    )
    assert result.exit_code != 0


def test_corpus_author_display_name_uses_display_name_attr(
    tmp_path: Path,
) -> None:
    """Graph stores display_name, not name. Regression test."""
    corpus = _make_corpus(tmp_path / "c")
    from wikify.ingest.pipeline import refresh_corpus
    refresh_corpus(corpus)
    # Fixture authors are e.g. "author_0" — _author_key normalises and the
    # display_name is the original. Run `find --by author` and check the
    # name column is populated, not blank.
    result = runner.invoke(
        app,
        [
            "corpus", "find",
            "--corpus", str(corpus.root),
            "--by", "author", "--rank", "h_index", "--top-k", "5",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["items"], "expected at least one author"
    # At least one row should have a non-empty name (display_name).
    assert any(item.get("name") for item in data["items"]), (
        f"all authors have blank name; items={data['items']}"
    )


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


def test_corpus_show_handles_windows_crlf_handle(tmp_path: Path) -> None:
    """Handles received from a `--format quiet` pipe on Windows include `\\r`.

    `parse_handle` must strip surrounding whitespace so the documented
    `traverse … | xargs traverse …` pattern works on every platform.
    """
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        ["corpus", "show", "doc:paper_0\r", "--corpus", str(corpus.root)],
    )
    assert result.exit_code == 0, result.output
    assert "Title 0" in result.output


def test_corpus_cli_reads_sqlite_when_json_sidecars_absent(
    tmp_path: Path,
) -> None:
    corpus = _make_sqlite_only_corpus(tmp_path / "c")

    docs = runner.invoke(
        app,
        ["corpus", "list", "docs", "--corpus", str(corpus.root), "--format", "json"],
    )
    chunks = runner.invoke(
        app,
        [
            "corpus", "list", "chunks",
            "--corpus", str(corpus.root),
            "--doc", "paper_0",
        ],
    )
    show_doc = runner.invoke(
        app,
        ["corpus", "show", "doc:paper_0", "--corpus", str(corpus.root)],
    )
    show_chunk = runner.invoke(
        app,
        [
            "corpus", "show", "chunk:paper_0__c0000",
            "--corpus", str(corpus.root),
            "--full",
        ],
    )
    find = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text",
            "--top-k", "2",
            "--format", "quiet",
        ],
    )

    assert docs.exit_code == 0, docs.output
    assert json.loads(docs.output)["items"] == ["doc:paper_0", "doc:paper_1"]
    assert chunks.exit_code == 0, chunks.output
    assert "paper_0__c0000" in chunks.output
    assert show_doc.exit_code == 0, show_doc.output
    assert "Title 0" in show_doc.output
    assert show_chunk.exit_code == 0, show_chunk.output
    assert "atomic layer deposition" in show_chunk.output
    assert find.exit_code == 0, find.output
    assert find.output.splitlines() == ["chunk:paper_0__c0000", "chunk:paper_0__c0001"]


def test_corpus_find_rejects_zero_top_k(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "0",
        ],
    )
    assert result.exit_code != 0
    assert "bad_int" in (result.output + result.stderr)


def test_corpus_find_rejects_negative_top_k(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "find", "atomic layer",
            "--corpus", str(corpus.root),
            "--text", "--top-k", "-3",
        ],
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


# ----------------------------------------------------------------- sample
#
# `corpus sample` requires a fully embedded corpus with graph metrics.
# The bare `_make_corpus`
# fixture deliberately omits these — they are exercised end-to-end by
# `test_baseline_sampling.py`. The tests below cover the CLI surface
# itself: validation gates, the strategy-rejection envelope, --explain,
# and the JSON/quiet emitters via a monkeypatched `queries.sample_docs`.


def test_corpus_sample_explain_short_circuits_before_loading(
    tmp_path: Path,
) -> None:
    """`--explain` must print the resolved chain and exit without
    touching vectors/KG, so it works on an unembedded corpus."""
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "5",
            "--strategy", "diverse",
            "--pagerank-weight", "0.7",
            "--explain",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sample_diverse(max_docs=5" in result.output
    assert "pagerank_weight=0.7" in result.output


def test_corpus_sample_rejects_zero_max(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "0",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"] == "bad_int"
    assert "--max must be > 0" in payload["message"]


def test_corpus_sample_rejects_negative_max(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "-3",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stderr)
    assert payload["error"] == "bad_int"


def test_corpus_sample_rejects_unknown_strategy(tmp_path: Path) -> None:
    """Strategy validation lives in `queries.sample_docs` and runs
    before any vector/KG load, so it surfaces cleanly even on an
    unembedded fixture corpus."""
    corpus = _make_corpus(tmp_path / "c")
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "5",
            "--strategy", "random",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.stderr)
    assert payload["error"] == "bad_strategy"
    assert "random" in payload["message"]


def test_corpus_sample_emits_json_envelope_and_doc_handles(
    tmp_path: Path, monkeypatch
) -> None:
    """JSON output is `{"ok": True, "items": [...]}` with `doc_handle`
    derived from each sampled doc id. Patches the queries layer so the
    test does not need an embedded corpus."""
    from wikify.cli import corpus as cli_corpus

    corpus = _make_corpus(tmp_path / "c")
    monkeypatch.setattr(
        cli_corpus.queries,
        "sample_docs",
        lambda corpus, **kw: ["paper_0", "paper_1"],
    )
    monkeypatch.setattr(
        cli_corpus.queries,
        "doc_metrics",
        lambda corpus, ids: {
            did: {"citation_count": 7, "pagerank": 0.0123} for did in ids
        },
    )
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "2",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    handles = [item["doc_handle"] for item in payload["items"]]
    assert handles == ["doc:paper_0", "doc:paper_1"]
    assert payload["items"][0]["citation_count"] == 7
    assert payload["items"][0]["pagerank"] == 0.0123


def test_corpus_sample_quiet_emits_one_handle_per_line(
    tmp_path: Path, monkeypatch
) -> None:
    from wikify.cli import corpus as cli_corpus

    corpus = _make_corpus(tmp_path / "c")
    monkeypatch.setattr(
        cli_corpus.queries,
        "sample_docs",
        lambda corpus, **kw: ["paper_0", "paper_1"],
    )
    monkeypatch.setattr(
        cli_corpus.queries,
        "doc_metrics",
        lambda corpus, ids: {did: {} for did in ids},
    )
    result = runner.invoke(
        app,
        [
            "corpus", "sample",
            "--corpus", str(corpus.root),
            "--max", "2",
            "--format", "quiet",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["doc:paper_0", "doc:paper_1"]
