"""``evidence.jsonl`` — append-only evidence ledger per concept.

One JSONL file per concept folder. Each record is an
``EvidenceRecord``: chunk_id + doc_id + quote + score + status. The
status field is the only mutable column — re-appending a chunk_id
with status="archived" supersedes the active record at dedup time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

from ...api import Bundle


class EvidenceRecord(BaseModel):
    """One line in ``work/concepts/<slug>/evidence.jsonl``."""

    chunk_id: str
    doc_id: str
    quote: str = ""
    score: float = 0.0
    status: str = "active"  # "active" | "archived"
    used_in_page: bool = False
    note: str = ""

    model_config = {"extra": "allow"}


def evidence_path(bundle: Bundle, slug: str) -> Path:
    return bundle.work_concept_dir(slug) / "evidence.jsonl"


def append_evidence(
    bundle: Bundle, slug: str, records: Iterable[EvidenceRecord | dict]
) -> int:
    """Append one or more records to the concept's evidence.jsonl.

    Returns the count appended. Caller is responsible for serialising
    cross-process writes through the run/concept lock.
    """
    p = evidence_path(bundle, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a", encoding="utf-8") as fh:
        for r in records:
            if isinstance(r, dict):
                r = EvidenceRecord.model_validate(r)
            fh.write(r.model_dump_json() + "\n")
            n += 1
    return n


def read_evidence(bundle: Bundle, slug: str) -> list[EvidenceRecord]:
    """Read every record. Skips unparseable lines."""
    p = evidence_path(bundle, slug)
    if not p.exists():
        return []
    out: list[EvidenceRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(EvidenceRecord.model_validate_json(line))
        except Exception:
            continue
    return out


def dedup_evidence(bundle: Bundle, slug: str) -> int:
    """Rewrite the ledger so each chunk_id appears once (latest wins).

    Returns the number of records dropped. Atomic: writes to a sibling
    temp file and ``os.replace``.
    """
    import os
    import tempfile

    records = read_evidence(bundle, slug)
    by_chunk: dict[str, EvidenceRecord] = {}
    for r in records:
        by_chunk[r.chunk_id] = r
    dropped = len(records) - len(by_chunk)
    if dropped == 0:
        return 0
    p = evidence_path(bundle, slug)
    fd, tmp = tempfile.mkstemp(prefix=".evidence-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in by_chunk.values():
                fh.write(r.model_dump_json() + "\n")
        os.replace(tmp, p)
    except Exception:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise
    return dropped
