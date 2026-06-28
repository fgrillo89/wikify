"""Tests for ``wikify.bundle.work.coverage`` — chunk coverage ratio."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle, Corpus
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.coverage import compute_coverage, residual_chunk_ids
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence
from wikify.bundle.work.notebook import init_notebook, read_notebook, save_notebook
from wikify.cli import app

runner = CliRunner()


def _make_corpus(corpus_dir: Path, chunks: list[tuple[str, str]]) -> Corpus:
    """Create a corpus dir with a SQLite chunks table populated from ``chunks``.

    ``chunks`` is a list of ``(chunk_id, doc_id)``.
    """
    corpus_dir.mkdir(parents=True, exist_ok=True)
    db = corpus_dir / "wikify.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER"
        ")"
    )
    for i, (cid, did) in enumerate(chunks):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, did, i, f"body for {cid}", "abstract", 0),
        )
    con.commit()
    con.close()
    return Corpus(root=corpus_dir)


def _make_bundle(bundle_dir: Path) -> Bundle:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "run").mkdir()
    b = Bundle(root=bundle_dir)
    init_run(b, corpus_path="data/corpora/foo")
    return b


def test_compute_coverage_empty_bundle(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path / "corpus",
        [("c1", "d1"), ("c2", "d1"), ("c3", "d2")],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    report = compute_coverage(bundle, corpus)
    assert report.n_total == 3
    assert report.n_covered == 0
    assert report.chunk_coverage_ratio == 0.0


def test_compute_coverage_counts_in_flight_notebook(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path / "corpus",
        [("c1", "d1"), ("c2", "d1"), ("c3", "d2"), ("c4", "d2")],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1", "c3"]
    save_notebook(bundle, "alpha", nb)
    report = compute_coverage(bundle, corpus)
    assert report.n_total == 4
    assert report.n_covered == 2
    assert report.n_covered_in_flight == 2
    assert report.chunk_coverage_ratio == 0.5


def test_compute_coverage_unions_notebook_and_evidence_for_same_slug(
    tmp_path: Path,
) -> None:
    """Regression: evidence.jsonl chunks must count even when a notebook
    exists for the same slug. The explorer may lag the notebook by one
    round; coverage should not dip while that lag persists.
    """
    corpus = _make_corpus(
        tmp_path / "corpus",
        [("c1", "d1"), ("c2", "d1"), ("c3", "d2")],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1"]
    save_notebook(bundle, "alpha", nb)
    # Explorer appended c2 to the ledger but has not folded it into
    # the notebook frontmatter yet.
    append_evidence(
        bundle, "alpha",
        [EvidenceRecord(chunk_id="c2", doc_id="d1", status="active")],
    )
    report = compute_coverage(bundle, corpus)
    assert report.n_covered == 2  # c1 (notebook) union c2 (ledger)


def test_compute_coverage_counts_evidence_jsonl_fallback(tmp_path: Path) -> None:
    """Baseline-era bundles have evidence.jsonl but no notebook.md."""
    corpus = _make_corpus(
        tmp_path / "corpus", [("c1", "d1"), ("c2", "d1")]
    )
    bundle = _make_bundle(tmp_path / "bundle")
    bundle.work_concept_dir("baseline").mkdir(parents=True, exist_ok=True)
    append_evidence(
        bundle, "baseline",
        [EvidenceRecord(chunk_id="c1", doc_id="d1", status="active")],
    )
    report = compute_coverage(bundle, corpus)
    assert report.n_covered == 1


def test_residual_chunk_ids(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path / "corpus",
        [("c1", "d1"), ("c2", "d1"), ("c3", "d2")],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1"]
    save_notebook(bundle, "alpha", nb)
    assert residual_chunk_ids(bundle, corpus) == {"c2", "c3"}


def test_compute_coverage_per_doc_breakdown(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path / "corpus",
        [("c1", "d1"), ("c2", "d1"), ("c3", "d2")],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1", "c3"]
    save_notebook(bundle, "alpha", nb)
    report = compute_coverage(bundle, corpus)
    assert report.per_doc["d1"]["ratio"] == 0.5
    assert report.per_doc["d2"]["ratio"] == 1.0


def _make_corpus_typed(
    corpus_dir: Path, chunks: list[tuple[str, str, str, int]]
) -> Corpus:
    """Corpus builder with explicit ``(chunk_id, doc_id, section_type, is_boilerplate)``."""
    corpus_dir.mkdir(parents=True, exist_ok=True)
    db = corpus_dir / "wikify.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER"
        ")"
    )
    for i, (cid, did, stype, boiler) in enumerate(chunks):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, did, i, f"body for {cid}", stype, boiler),
        )
    con.commit()
    con.close()
    return Corpus(root=corpus_dir)


def test_compute_coverage_addressable_excludes_structural(tmp_path: Path) -> None:
    """Addressable denominator drops captions/references/boilerplate; raw keeps them."""
    corpus = _make_corpus_typed(
        tmp_path / "corpus",
        [
            ("c1", "d1", "body", 0),        # addressable
            ("c2", "d1", "caption", 0),     # excluded (structural)
            ("c3", "d2", "references", 0),  # excluded (structural)
            ("c4", "d2", "body", 1),        # excluded (is_boilerplate)
        ],
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1", "c2"]  # one body, one caption
    save_notebook(bundle, "alpha", nb)
    report = compute_coverage(bundle, corpus)
    assert report.n_total == 4
    assert report.n_covered == 2
    assert report.chunk_coverage_ratio == 0.5
    # Only c1 is addressable; covering it is full addressable coverage.
    assert report.n_addressable == 1
    assert report.n_addressable_covered == 1
    assert report.addressable_coverage_ratio == 1.0


def test_compute_coverage_addressable_falls_back_without_columns(tmp_path: Path) -> None:
    """A corpus lacking section_type/is_boilerplate treats every chunk as addressable."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(corpus_dir / "wikify.db"))
    con.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT)")
    con.executemany(
        "INSERT INTO chunks VALUES (?, ?)", [("c1", "d1"), ("c2", "d1")]
    )
    con.commit()
    con.close()
    corpus = Corpus(root=corpus_dir)
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1"]
    save_notebook(bundle, "alpha", nb)
    report = compute_coverage(bundle, corpus)
    assert report.n_addressable == 2
    assert report.addressable_coverage_ratio == 0.5


def test_cli_coverage_json_output(tmp_path: Path) -> None:
    corpus = _make_corpus(
        tmp_path / "corpus", [("c1", "d1"), ("c2", "d1")]
    )
    bundle = _make_bundle(tmp_path / "bundle")
    init_notebook(bundle, slug="alpha", kind="article")
    nb = read_notebook(bundle, "alpha")
    nb.front.provenance.covered_chunks = ["c1"]
    save_notebook(bundle, "alpha", nb)
    res = runner.invoke(
        app,
        [
            "work", "coverage",
            "--run", str(bundle.root),
            "--corpus", str(corpus.root),
            "--format", "json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["n_total"] == 2
    assert payload["n_covered"] == 1
    assert payload["chunk_coverage_ratio"] == 0.5
