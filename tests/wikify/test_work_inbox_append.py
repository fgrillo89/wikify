"""Tests for wikify.bundle.work.inbox — inbox append + read."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Bundle
from wikify.bundle.work.inbox import (
    append_inbox,
    append_inbox_records,
    inbox_path,
    list_inbox_files,
    read_inbox,
    truncate_inbox,
)


def _v2(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle.open(tmp_path)


def test_append_creates_directory(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_inbox(bundle, "evidence_suggestions", {"chunk_id": "d1:001", "concept": "ALD"})
    assert bundle.work_inbox_dir.is_dir()
    assert (bundle.work_inbox_dir / "evidence_suggestions.jsonl").is_file()


def test_append_then_read(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_inbox(bundle, "evidence_suggestions", {"chunk_id": "d1:001"})
    append_inbox(bundle, "evidence_suggestions", {"chunk_id": "d1:002"})
    records = read_inbox(bundle, "evidence_suggestions")
    assert len(records) == 2
    assert records[0]["chunk_id"] == "d1:001"


def test_append_records_batch(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    n = append_inbox_records(
        bundle,
        "concept_suggestions",
        [{"title": "ALD"}, {"title": "CVD"}],
    )
    assert n == 2
    assert len(read_inbox(bundle, "concept_suggestions")) == 2


def test_append_invalid_kind_raises(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    with pytest.raises(ValueError, match="unknown inbox kind"):
        append_inbox(bundle, "not_a_kind", {})


def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_inbox(bundle, "merge_suggestions", {"a": "x"})
    p = inbox_path(bundle, "merge_suggestions")
    with p.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    append_inbox(bundle, "merge_suggestions", {"b": "y"})
    records = read_inbox(bundle, "merge_suggestions")
    assert len(records) == 2


def test_truncate_clears_records(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_inbox(bundle, "query_feedback", {"q": "1"})
    append_inbox(bundle, "query_feedback", {"q": "2"})
    n = truncate_inbox(bundle, "query_feedback")
    assert n == 2
    assert read_inbox(bundle, "query_feedback") == []


def test_list_inbox_files(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    append_inbox(bundle, "evidence_suggestions", {"x": 1})
    append_inbox(bundle, "concept_suggestions", {"y": 2})
    files = list_inbox_files(bundle)
    assert "evidence_suggestions.jsonl" in files
    assert "concept_suggestions.jsonl" in files


def test_sequential_writes_preserve_order(tmp_path: Path) -> None:
    """Sequential appends preserve order in the JSONL ledger.

    The cross-process / cross-thread atomicity case is intentionally not
    tested here: ``O_APPEND`` is not atomic on Windows and the
    inbox.py module docstring calls out that the per-writer split
    (one inbox file per writer + merge during tend) is the
    portable concurrency model. That follow-up is deferred.
    """
    bundle = _v2(tmp_path)
    for i in range(20):
        append_inbox(bundle, "evidence_suggestions", {"i": i})
    records = read_inbox(bundle, "evidence_suggestions")
    assert len(records) == 20
    assert [r["i"] for r in records] == list(range(20))
