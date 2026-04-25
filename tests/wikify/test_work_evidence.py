"""Tests for wikify.bundle.work.evidence — evidence.jsonl ledger."""

from __future__ import annotations

from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.evidence import (
    EvidenceRecord,
    append_evidence,
    dedup_evidence,
    read_evidence,
)


def _v2_with_concept(tmp_path: Path, slug: str = "ald") -> tuple[Bundle, str]:
    (tmp_path / "run").mkdir(parents=True)
    bundle = Bundle.open(tmp_path)
    s, _ = create_concept(bundle, page_id="ALD", slug=slug)
    return bundle, s


def test_append_then_read(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    rec = EvidenceRecord(chunk_id="d1:001", doc_id="d1", quote="x", score=0.91)
    n = append_evidence(bundle, slug, [rec])
    assert n == 1
    records = read_evidence(bundle, slug)
    assert len(records) == 1
    assert records[0].chunk_id == "d1:001"


def test_append_dict_records(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    n = append_evidence(
        bundle,
        slug,
        [
            {"chunk_id": "d1:001", "doc_id": "d1"},
            {"chunk_id": "d1:002", "doc_id": "d1"},
        ],
    )
    assert n == 2
    assert len(read_evidence(bundle, slug)) == 2


def test_dedup_keeps_latest(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="d1:001", doc_id="d1", status="active"),
            EvidenceRecord(chunk_id="d1:002", doc_id="d1"),
            EvidenceRecord(chunk_id="d1:001", doc_id="d1", status="archived"),
        ],
    )
    dropped = dedup_evidence(bundle, slug)
    assert dropped == 1
    records = read_evidence(bundle, slug)
    assert len(records) == 2
    statuses = {r.chunk_id: r.status for r in records}
    assert statuses["d1:001"] == "archived"


def test_dedup_no_op_when_unique(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="d1:001", doc_id="d1"),
            EvidenceRecord(chunk_id="d1:002", doc_id="d1"),
        ],
    )
    assert dedup_evidence(bundle, slug) == 0


def test_read_skips_corrupt_lines(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    append_evidence(bundle, slug, [EvidenceRecord(chunk_id="d1:001", doc_id="d1")])
    p = bundle.work_concept_dir(slug) / "evidence.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write("\n")
    append_evidence(bundle, slug, [EvidenceRecord(chunk_id="d1:002", doc_id="d1")])
    records = read_evidence(bundle, slug)
    assert len(records) == 2


def test_read_empty_when_no_file(tmp_path: Path) -> None:
    bundle, slug = _v2_with_concept(tmp_path)
    # Concept created but no evidence appended yet.
    assert read_evidence(bundle, slug) == []
