"""build-evidence corpus-find top-up: in-process + loud-on-failure.

Regression tests for the fix that replaced the ``find_chunks`` subprocess
shell-out (which parsed ``wikify corpus find --format json`` stdout and
silently returned ``[]`` when any library polluted stdout) with a direct
in-process ``queries.find`` call that fails loudly instead of degrading a
page to seed-only evidence.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.evidence import read_evidence
from wikify.cli import app
from wikify.corpus import queries

runner = CliRunner()

_LONG = "This is a sufficiently long chunk body about atomic layer deposition " \
        "growth per cycle so it clears the minimum-chars filter easily."


def _make_corpus(corpus_root: Path, chunks: list[tuple[str, str]]) -> None:
    """Minimal corpus SQLite: chunks with long body text, section_type=body."""
    corpus_root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(corpus_root / "wikify.db"))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER)"
    )
    for i, (cid, did) in enumerate(chunks):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, did, i, f"{_LONG} ({cid})", "body", 0),
        )
    con.commit()
    con.close()


def _bundle_with_concept(tmp_path: Path, corpus_root: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "run").mkdir(exist_ok=True)
    b = Bundle(root=bundle_dir)
    init_run(b, corpus_path=str(corpus_root))
    create_concept(b, page_id="ALD", kind="article")
    return bundle_dir


def test_build_evidence_uses_in_process_find(tmp_path: Path, monkeypatch) -> None:
    """The find top-up runs in-process (``queries.find``), with rank='all'
    and strict_semantic=True, and its rows are committed as evidence."""
    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("cfind1", "d1"), ("cfind2", "d1")])
    bundle_dir = _bundle_with_concept(tmp_path, corpus_root)

    seen: dict = {}

    def fake_find(corpus, **kwargs):
        seen.update(kwargs)
        return {
            "kind": "chunks",
            "rows": [
                {"id": "cfind1", "doc_id": "d1", "score": 0.9},
                {"id": "cfind2", "doc_id": "d1", "score": 0.8},
            ],
            "scored": True,
        }

    monkeypatch.setattr(queries, "find", fake_find)

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "ald",
            "--corpus", str(corpus_root),
            "--run", str(bundle_dir),
            "--target", "2",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["appended"] >= 1
    # Wiring: build-evidence asked for the multi-mode search in strict mode.
    assert seen.get("rank") == "all"
    assert seen.get("strict_semantic") is True
    committed = {r.chunk_id for r in read_evidence(Bundle.open(bundle_dir), "ald")}
    assert {"cfind1", "cfind2"} & committed


def test_build_evidence_queries_title_and_aliases(tmp_path: Path, monkeypatch) -> None:
    """The find top-up queries the title AND each non-author alias as a
    separate facet, so specific sub-topics surface papers a broad title query
    buries."""
    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("c1", "d1")])
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "run").mkdir(exist_ok=True)
    b = Bundle(root=bundle_dir)
    init_run(b, corpus_path=str(corpus_root))
    create_concept(
        b, page_id="Nucleation", kind="article",
        aliases=["Volmer-Weber growth", "island growth mode", "author:someone"],
    )

    queried: list[str] = []

    def fake_find(corpus, **kwargs):
        queried.append(kwargs.get("query"))
        return {"kind": "chunks", "rows": [], "scored": True}

    monkeypatch.setattr(queries, "find", fake_find)
    # find returns nothing, so the gather queries every facet across all
    # widening passes (and then reports no_evidence, exit 1 -- expected). What
    # matters here is which queries were issued.
    runner.invoke(
        app,
        [
            "work", "build-evidence", "nucleation",
            "--corpus", str(corpus_root), "--run", str(bundle_dir),
            "--target", "5", "--format", "json",
        ],
    )
    assert "Nucleation" in queried
    assert "Volmer-Weber growth" in queried
    assert "island growth mode" in queried
    # author: aliases are not used as search facets.
    assert "author:someone" not in queried
    assert not any("someone" == q for q in queried)


def test_build_evidence_json_survives_stdout_noise(tmp_path: Path, monkeypatch) -> None:
    """In-process search must not let library stdout noise (e.g. a mismatched
    onnxruntime warning) corrupt ``build-evidence --format json`` output."""
    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("cfind1", "d1")])
    bundle_dir = _bundle_with_concept(tmp_path, corpus_root)

    def noisy_find(corpus, **kwargs):
        # Simulate a library printing a warning to stdout during embedding.
        print("\x1b[33m[W:onnxruntime] provider mismatch; falling back\x1b[0m")
        return {
            "kind": "chunks",
            "rows": [{"id": "cfind1", "doc_id": "d1", "score": 0.9}],
            "scored": True,
        }

    monkeypatch.setattr(queries, "find", noisy_find)

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "ald",
            "--corpus", str(corpus_root),
            "--run", str(bundle_dir),
            "--target", "1",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    # stdout must be clean JSON despite the noise emitted during the search.
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert "onnxruntime" not in result.stdout


def test_build_evidence_fails_loud_on_search_error(tmp_path: Path, monkeypatch) -> None:
    """A search failure (e.g. broken embedder) must fail loudly, not silently
    produce a seed-only page."""
    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("cfind1", "d1")])
    bundle_dir = _bundle_with_concept(tmp_path, corpus_root)

    def raising_find(corpus, **kwargs):
        raise queries.QueryError("semantic_search_failed", "embedder unavailable")

    monkeypatch.setattr(queries, "find", raising_find)

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "ald",
            "--corpus", str(corpus_root),
            "--run", str(bundle_dir),
            "--target", "14",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    # cli_error writes the structured envelope to stderr.
    data = json.loads(result.stderr)
    assert data["ok"] is False
    assert data["error"] == "corpus_search_failed"
    # No page was written on the degraded path.
    assert read_evidence(Bundle.open(bundle_dir), "ald") == []


def test_build_evidence_json_error_survives_stdout_noise(
    tmp_path: Path, monkeypatch
) -> None:
    """A search that prints to stdout AND then fails must not corrupt the JSON
    error envelope on stderr; the noise is folded into the payload instead."""
    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("cfind1", "d1")])
    bundle_dir = _bundle_with_concept(tmp_path, corpus_root)

    def noisy_raising_find(corpus, **kwargs):
        print("\x1b[33m[W:onnxruntime] provider mismatch; falling back\x1b[0m")
        raise queries.QueryError("semantic_search_failed", "embedder unavailable")

    monkeypatch.setattr(queries, "find", noisy_raising_find)

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "ald",
            "--corpus", str(corpus_root),
            "--run", str(bundle_dir),
            "--target", "14",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    # stderr must be clean, parseable JSON despite the noise printed mid-search.
    data = json.loads(result.stderr)
    assert data["ok"] is False
    assert data["error"] == "corpus_search_failed"
    # The captured noise is preserved in the payload, not prepended to it.
    assert "onnxruntime" in data.get("search_diagnostics", "")


def test_build_evidence_normalizes_non_queryerror(tmp_path: Path, monkeypatch) -> None:
    """A non-QueryError search failure (e.g. a sqlite error from a broken
    store) is normalized to the structured envelope, not a raw traceback."""
    import sqlite3

    corpus_root = tmp_path / "corpus"
    _make_corpus(corpus_root, [("cfind1", "d1")])
    bundle_dir = _bundle_with_concept(tmp_path, corpus_root)

    def broken_find(corpus, **kwargs):
        raise sqlite3.OperationalError("no such table: chunks")

    monkeypatch.setattr(queries, "find", broken_find)

    result = runner.invoke(
        app,
        [
            "work", "build-evidence", "ald",
            "--corpus", str(corpus_root),
            "--run", str(bundle_dir),
            "--target", "14",
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.stderr)
    assert data["ok"] is False
    assert data["error"] == "corpus_search_failed"
    assert "OperationalError" in data["message"]
