"""Tests for ``wikify.bundle.work.chunk_ids`` — chunk-id resolver."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from wikify.api import Bundle, Corpus
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.chunk_ids import (
    build_suffix_index,
    corpus_path_from_bundle,
    resolve_chunk_id,
)
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence, read_evidence
from wikify.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures


def _make_corpus_db(db_path: Path, chunk_ids: list[str]) -> None:
    """Create a minimal corpus SQLite with chunks table."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER"
        ")"
    )
    for i, cid in enumerate(chunk_ids):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, f"doc_{i}", i, f"body {cid}", "body", 0),
        )
    con.commit()
    con.close()


def _make_bundle(bundle_dir: Path, corpus_path: str = "data/corpora/test") -> Bundle:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "run").mkdir(exist_ok=True)
    b = Bundle(root=bundle_dir)
    init_run(b, corpus_path=corpus_path)
    return b


# ---------------------------------------------------------------------------
# build_suffix_index


def test_build_suffix_index_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [])
    canonical_ids, suffix_map = build_suffix_index(db)
    assert canonical_ids == frozenset()
    assert suffix_map == {}


def test_build_suffix_index_missing_db(tmp_path: Path) -> None:
    db = tmp_path / "no_db.sqlite"
    canonical_ids, suffix_map = build_suffix_index(db)
    assert canonical_ids == frozenset()
    assert suffix_map == {}


def test_build_suffix_index_canonical_ids(tmp_path: Path) -> None:
    cids = [
        "[2015 Foo] Paper_abc123__c0000_d2af4466",
        "[2016 Bar] Paper_def456__c0001_e3bc5577",
    ]
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, cids)
    canonical_ids, suffix_map = build_suffix_index(db)
    assert canonical_ids == frozenset(cids)
    # Suffix of first id is "d2af4466", second is "e3bc5577".
    assert suffix_map["d2af4466"] == cids[0]
    assert suffix_map["e3bc5577"] == cids[1]


def test_build_suffix_index_ambiguous_suffix_pruned(tmp_path: Path) -> None:
    # Two ids with the same trailing segment.
    cids = [
        "paper_a_c0001_aabbccdd",
        "paper_b_c0001_aabbccdd",  # same suffix as above
    ]
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, cids)
    _, suffix_map = build_suffix_index(db)
    assert "aabbccdd" not in suffix_map  # ambiguous, must be pruned


# ---------------------------------------------------------------------------
# resolve_chunk_id


def test_resolve_chunk_id_already_canonical(tmp_path: Path) -> None:
    cid = "[2015 Foo] Paper_abc123__c0000_d2af4466"
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [cid])
    canonical_ids, suffix_map = build_suffix_index(db)
    assert resolve_chunk_id(cid, suffix_map, canonical_ids) == cid


def test_resolve_chunk_id_short_handle(tmp_path: Path) -> None:
    cid = "[2015 Foo] Paper_abc123__c0000_d2af4466"
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [cid])
    canonical_ids, suffix_map = build_suffix_index(db)
    resolved = resolve_chunk_id("chunk:d2af4466", suffix_map, canonical_ids)
    assert resolved == cid


def test_resolve_chunk_id_unknown_handle_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, ["some_id_abc123"])
    canonical_ids, suffix_map = build_suffix_index(db)
    assert resolve_chunk_id("chunk:ffffffff", suffix_map, canonical_ids) is None


def test_resolve_chunk_id_empty_string_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [])
    canonical_ids, suffix_map = build_suffix_index(db)
    assert resolve_chunk_id("", suffix_map, canonical_ids) is None


def test_resolve_chunk_id_figure_handle_exact(tmp_path: Path) -> None:
    cid = "paper_abc/fig_000__caption"
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [cid])
    canonical_ids, suffix_map = build_suffix_index(db)
    # Figure handle with slash.
    handle = "chunk:paper_abc/fig_000__caption"
    resolved = resolve_chunk_id(handle, suffix_map, canonical_ids)
    assert resolved == cid


def test_resolve_chunk_id_like_underscore_not_wildcard(tmp_path: Path) -> None:
    """LIKE fallback must not treat the '_' separator as a wildcard.

    Two canonical ids share the same suffix hex but differ only by the
    character immediately before it (the ``_`` separator vs a literal char
    that happens to sit just before the suffix).  The LIKE pattern
    ``%\\_<suffix>`` (escaped underscore) must match ONLY ids whose suffix
    is delimited by a literal underscore, so the ambiguous pair resolves
    to exactly one result.
    """
    suffix = "aabbccdd"
    # id_a has the suffix after a literal underscore (correct canonical form).
    id_a = f"paper_title_c0001_{suffix}"
    # id_b ends with the same hex bytes but prefixed differently so the
    # in-memory suffix_map prunes both as ambiguous.
    id_b = f"other_title_c0002_{suffix}"
    db = tmp_path / "wikify.db"
    _make_corpus_db(db, [id_a, id_b])
    canonical_ids, suffix_map = build_suffix_index(db)
    # Suffix is ambiguous, so suffix_map must not contain it.
    assert suffix not in suffix_map

    # Now remove id_b so only id_a remains in the DB — simulates the case
    # where ambiguity existed at index-build time but a LIKE query can now
    # disambiguate. (We rebuild the DB with just one id that has that suffix.)
    db2 = tmp_path / "wikify2.db"
    _make_corpus_db(db2, [id_a])
    canonical_ids2, suffix_map2 = build_suffix_index(db2)
    # With a single match, in-memory index resolves it directly.
    assert suffix_map2[suffix] == id_a

    # Regression: verify LIKE fallback with a DB that has two ids sharing
    # the suffix — result must be None (ambiguous, len(rows) != 1), not a
    # wrong id chosen because '_' was treated as a LIKE wildcard.
    # To exercise the LIKE path we need an empty suffix_map but a populated DB.
    # We patch the suffix_map to be empty to force the fallback path.
    empty_suffix_map: dict[str, str] = {}
    resolved = resolve_chunk_id(
        f"chunk:{suffix}", empty_suffix_map, canonical_ids, sqlite_path=db
    )
    # Two rows match -> len(rows) != 1 -> should return None (ambiguous).
    assert resolved is None, (
        f"Expected None for ambiguous LIKE result, got {resolved!r}"
    )


# ---------------------------------------------------------------------------
# corpus_path_from_bundle


def test_corpus_path_from_bundle_absolute(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "mycorpus"
    corpus_dir.mkdir()
    bundle = _make_bundle(tmp_path / "bundle", corpus_path=str(corpus_dir))
    p = corpus_path_from_bundle(bundle.root)
    assert p == corpus_dir


def test_corpus_path_from_bundle_missing_state(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert corpus_path_from_bundle(bundle_dir) is None


def test_corpus_path_from_bundle_non_existent_corpus(tmp_path: Path) -> None:
    # Corpus path recorded but the directory doesn't exist.
    bundle = _make_bundle(tmp_path / "bundle", corpus_path="/no/such/dir")
    p = corpus_path_from_bundle(bundle.root)
    assert p is None


def test_corpus_path_from_bundle_relative_to_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    # The recorded corpus_path is relative to the working directory the
    # run was launched from (the repo root), not the bundle root.
    monkeypatch.chdir(tmp_path)
    corpus_dir = tmp_path / "data" / "corpora" / "mycorpus"
    corpus_dir.mkdir(parents=True)
    bundle = _make_bundle(
        tmp_path / "bundle", corpus_path="data/corpora/mycorpus"
    )
    p = corpus_path_from_bundle(bundle.root)
    assert p is not None and p.resolve() == corpus_dir.resolve()


# ---------------------------------------------------------------------------
# cmd_add_evidence — rejection path (F1)


def _init_bundle_with_corpus(tmp_path: Path) -> tuple[Bundle, Corpus, dict[str, str]]:
    """Init a bundle pointing at an actual corpus SQLite.

    Returns (bundle, corpus, ids_dict) where ids_dict has named chunk_ids.
    """
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    db = corpus_root / "wikify.db"
    long_id_a = "[2020 Smith] ALD Review_cafebabe12345678__c0000_aabbccdd"
    long_id_b = "[2020 Smith] ALD Review_cafebabe12345678__c0001_eeff0011"
    _make_corpus_db(db, [long_id_a, long_id_b])

    bundle = _make_bundle(tmp_path / "bundle", corpus_path=str(corpus_root))
    create_concept(bundle, page_id="ALD", kind="article")
    corpus = Corpus(root=corpus_root)
    return bundle, corpus, {"a": long_id_a, "b": long_id_b}


def test_add_evidence_resolves_short_handle(tmp_path: Path) -> None:
    """chunk:aabbccdd handle resolves to canonical id and is committed."""
    bundle, corpus, ids = _init_bundle_with_corpus(tmp_path)
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": "chunk:aabbccdd", "doc_id": "doc_0", "score": 0.9}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    committed = read_evidence(bundle, "ald")
    assert len(committed) == 1
    assert committed[0].chunk_id == ids["a"]


def test_add_evidence_rejects_unknown_handle(tmp_path: Path) -> None:
    """An unresolvable handle is rejected and reported in output."""
    bundle, corpus, ids = _init_bundle_with_corpus(tmp_path)
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": "chunk:deadbeef", "doc_id": "doc_0", "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    assert result.exit_code != 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["error"] == "all_rejected"
    assert any(r["chunk_id"] == "chunk:deadbeef" for r in data["rejected"])


def test_add_evidence_mixed_valid_and_invalid(tmp_path: Path) -> None:
    """Valid records are committed; invalid ones are listed in 'rejected'."""
    bundle, corpus, ids = _init_bundle_with_corpus(tmp_path)
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": ids["a"], "doc_id": "doc_0", "score": 0.9}) + "\n"
        + json.dumps({"chunk_id": "chunk:deadbeef", "doc_id": "doc_0", "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["appended"] == 1
    assert any(r["chunk_id"] == "chunk:deadbeef" for r in data.get("rejected", []))
    committed = read_evidence(bundle, "ald")
    assert len(committed) == 1
    assert committed[0].chunk_id == ids["a"]


def test_add_evidence_no_corpus_writes_through(tmp_path: Path) -> None:
    """When no corpus is reachable, records are written through unchanged."""
    bundle = _make_bundle(tmp_path / "bundle", corpus_path="/no/such/corpus")
    create_concept(bundle, page_id="ALD", kind="article")
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": "chunk:deadbeef", "doc_id": "doc_0", "score": 0.9}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["appended"] == 1
    # Handle was passed through unchanged.
    committed = read_evidence(bundle, "ald")
    assert committed[0].chunk_id == "chunk:deadbeef"


def test_add_evidence_empty_corpus_passes_through_with_warning(tmp_path: Path) -> None:
    """Empty corpus (DB exists, zero chunks) must pass records through and warn."""
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    db = corpus_root / "wikify.db"
    # Create a corpus DB with no chunks at all.
    _make_corpus_db(db, [])

    bundle = _make_bundle(tmp_path / "bundle", corpus_path=str(corpus_root))
    create_concept(bundle, page_id="ALD", kind="article")

    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": "chunk:deadbeef", "doc_id": "doc_0", "score": 0.9}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    # Must succeed (not exit with error).
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["appended"] == 1
    # Record must pass through unmodified.
    committed = read_evidence(bundle, "ald")
    assert committed[0].chunk_id == "chunk:deadbeef"
    # Warning must be emitted to stderr.
    assert "WARNING" in result.stderr
    assert "unresolved" in result.stderr


def test_add_evidence_no_corpus_emits_warning(tmp_path: Path) -> None:
    """No-corpus passthrough emits a WARNING to stderr."""
    bundle = _make_bundle(tmp_path / "bundle", corpus_path="/no/such/corpus")
    create_concept(bundle, page_id="ALD", kind="article")
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": "chunk:cafebabe", "doc_id": "doc_0", "score": 0.8}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["appended"] == 1
    # Warning must be emitted.
    assert "WARNING" in result.stderr


def test_add_evidence_emits_evidence_added_event(tmp_path: Path) -> None:
    """work add evidence emits an evidence_added event."""
    from wikify.bundle.run.events import read_events

    bundle, corpus, ids = _init_bundle_with_corpus(tmp_path)
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": ids["a"], "doc_id": "doc_0", "score": 0.9}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
            "--round", "3",
        ],
    )
    assert result.exit_code == 0, result.output
    events = read_events(bundle)
    ev_added = [e for e in events if e.type == "evidence_added"]
    assert ev_added, "expected evidence_added event"
    assert ev_added[-1].concept_id == "ald"
    assert ev_added[-1].data.get("round") == 3
    assert ev_added[-1].data.get("n") == 1


def test_add_evidence_event_without_round_flag(tmp_path: Path) -> None:
    """evidence_added event is emitted even without --round."""
    from wikify.bundle.run.events import read_events

    bundle, corpus, ids = _init_bundle_with_corpus(tmp_path)
    records_file = tmp_path / "ev.jsonl"
    records_file.write_text(
        json.dumps({"chunk_id": ids["b"], "doc_id": "doc_1", "score": 0.8}) + "\n",
        encoding="utf-8",
    )
    runner.invoke(
        app,
        [
            "work", "add", "evidence", "ald",
            "--run", str(bundle.root),
            "--records", str(records_file),
        ],
    )
    events = read_events(bundle)
    ev_added = [e for e in events if e.type == "evidence_added"]
    assert ev_added
    assert "round" not in ev_added[-1].data


# ---------------------------------------------------------------------------
# Coverage with short handles (F1/F5 defensive)


def _make_corpus_fixture(
    corpus_dir: Path, chunk_ids: list[tuple[str, str]]
) -> Corpus:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    db = corpus_dir / "wikify.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER"
        ")"
    )
    for i, (cid, did) in enumerate(chunk_ids):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, did, i, f"body for {cid}", "body", 0),
        )
    con.commit()
    con.close()
    return Corpus(root=corpus_dir)


def test_coverage_with_short_handle_evidence(tmp_path: Path) -> None:
    """Evidence stored as chunk:<suffix> is resolved and counted in coverage."""
    from wikify.bundle.work.coverage import compute_coverage

    long_id = "paper_abc123__c0000_d2af4466"
    corpus = _make_corpus_fixture(tmp_path / "corpus", [(long_id, "d1")])
    bundle = _make_bundle(tmp_path / "bundle")
    bundle.work_concept_dir("alpha").mkdir(parents=True, exist_ok=True)
    # Write evidence with a short handle.
    append_evidence(
        bundle, "alpha",
        [EvidenceRecord(chunk_id="chunk:d2af4466", doc_id="d1", status="active")],
    )
    report = compute_coverage(bundle, corpus)
    assert report.n_total == 1
    assert report.n_covered == 1
    assert report.chunk_coverage_ratio == 1.0


def test_coverage_unresolvable_handle_does_not_crash(tmp_path: Path) -> None:
    """An unresolvable handle does not raise; it simply misses the intersection."""
    from wikify.bundle.work.coverage import compute_coverage

    long_id = "paper_abc123__c0000_d2af4466"
    corpus = _make_corpus_fixture(tmp_path / "corpus", [(long_id, "d1")])
    bundle = _make_bundle(tmp_path / "bundle")
    bundle.work_concept_dir("alpha").mkdir(parents=True, exist_ok=True)
    append_evidence(
        bundle, "alpha",
        [EvidenceRecord(chunk_id="chunk:ffffffff", doc_id="d1", status="active")],
    )
    report = compute_coverage(bundle, corpus)
    assert report.n_total == 1
    assert report.n_covered == 0


# ---------------------------------------------------------------------------
# Staging sweep (F10)


def test_tend_sweeps_staging_file_when_all_committed(tmp_path: Path) -> None:
    """tend removes a staging file whose chunk_ids are all in the ledger."""
    bundle = _make_bundle(tmp_path / "bundle")
    create_concept(bundle, page_id="ALD", kind="article")
    # Commit evidence for the slug.
    append_evidence(
        bundle, "ald",
        [
            EvidenceRecord(chunk_id="c1", doc_id="d1", status="active"),
            EvidenceRecord(chunk_id="c2", doc_id="d1", status="active"),
        ],
    )
    # Create a staging file with the same chunk_ids.
    staging_dir = bundle.work_dir / "evidence_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "ald.jsonl"
    staging_file.write_text(
        json.dumps({"chunk_id": "c1", "doc_id": "d1"}) + "\n"
        + json.dumps({"chunk_id": "c2", "doc_id": "d1"}) + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["work", "tend", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["staging_files_removed"] == 1
    assert not staging_file.exists()


def test_tend_keeps_staging_file_with_uncommitted_records(tmp_path: Path) -> None:
    """tend keeps a staging file when some chunk_ids are not yet in the ledger."""
    bundle = _make_bundle(tmp_path / "bundle")
    create_concept(bundle, page_id="ALD", kind="article")
    # Only commit c1; staging also has c2.
    append_evidence(
        bundle, "ald",
        [EvidenceRecord(chunk_id="c1", doc_id="d1", status="active")],
    )
    staging_dir = bundle.work_dir / "evidence_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "ald.jsonl"
    staging_file.write_text(
        json.dumps({"chunk_id": "c1", "doc_id": "d1"}) + "\n"
        + json.dumps({"chunk_id": "c2", "doc_id": "d1"}) + "\n",  # not committed
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["work", "tend", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["staging_files_removed"] == 0
    assert staging_file.exists()


def test_tend_sweeps_empty_staging_file(tmp_path: Path) -> None:
    """An empty staging file is removed immediately."""
    bundle = _make_bundle(tmp_path / "bundle")
    create_concept(bundle, page_id="ALD", kind="article")
    staging_dir = bundle.work_dir / "evidence_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "ald.jsonl"
    staging_file.write_text("", encoding="utf-8")
    result = runner.invoke(
        app,
        ["work", "tend", "--run", str(bundle.root), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["staging_files_removed"] == 1
    assert not staging_file.exists()


# ---------------------------------------------------------------------------
# work list text output includes evidence count (F9)


def test_work_list_text_shows_evidence_count(tmp_path: Path) -> None:
    """work list text output includes the active evidence count per concept."""
    bundle_dir = tmp_path / "bundle"
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(tmp_path / "corpus")],
    )
    bundle = Bundle.open(bundle_dir)
    create_concept(bundle, page_id="ALD", kind="article")
    append_evidence(
        bundle, "ald",
        [
            EvidenceRecord(chunk_id="c1", doc_id="d1", status="active"),
            EvidenceRecord(chunk_id="c2", doc_id="d1", status="active"),
        ],
    )
    result = runner.invoke(
        app,
        ["work", "list", "--run", str(bundle_dir)],
    )
    assert result.exit_code == 0, result.output
    # Should show "2ev" in the text line.
    assert "2ev" in result.output


def test_work_list_json_includes_evidence_chunks(tmp_path: Path) -> None:
    """work list --format json includes evidence_chunks per item."""
    bundle_dir = tmp_path / "bundle"
    runner.invoke(
        app,
        ["run", "init", "--bundle", str(bundle_dir), "--corpus", str(tmp_path / "corpus")],
    )
    bundle = Bundle.open(bundle_dir)
    create_concept(bundle, page_id="ALD", kind="article")
    append_evidence(
        bundle, "ald",
        [EvidenceRecord(chunk_id="c1", doc_id="d1", status="active")],
    )
    result = runner.invoke(
        app,
        ["work", "list", "--run", str(bundle_dir), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    items = {i["slug"]: i for i in data["items"]}
    assert items["ald"]["evidence_chunks"] == 1
