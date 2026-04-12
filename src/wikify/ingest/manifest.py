"""Corpus manifest: tracks source records for incremental ingest.

The manifest records which source files have been ingested, their content
hashes, and the fingerprints of the parser/chunker/embedder that processed
them.  This lets the pipeline skip unchanged sources and detect when a
backend change requires a rebuild.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


@dataclass
class SourceRecord:
    """One ingested source file."""

    source_id: str  # stable identity (stem + content hash)
    source_path: str  # original path (informational)
    content_hash: str  # sha1[:12] of file bytes
    doc_id: str
    status: Literal["active", "deleted"] = "active"
    chunk_ids: list[str] = field(default_factory=list)
    parsed_at: str = ""  # ISO timestamp

    @staticmethod
    def now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class CorpusManifest:
    """Tracks all sources in a corpus for incremental ingest."""

    schema_version: int = 1
    corpus_id: str = ""
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    last_ingest: str = ""
    embedder_fingerprint: str = ""

    # --- persistence ---

    def save(self, path: Path) -> None:
        data = {
            "schema_version": self.schema_version,
            "corpus_id": self.corpus_id,
            "last_ingest": self.last_ingest,
            "embedder_fingerprint": self.embedder_fingerprint,
            "sources": {k: asdict(v) for k, v in self.sources.items()},
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> CorpusManifest:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = cls(
            schema_version=raw.get("schema_version", 1),
            corpus_id=raw.get("corpus_id", ""),
            last_ingest=raw.get("last_ingest", ""),
            embedder_fingerprint=raw.get("embedder_fingerprint", ""),
        )
        for key, src_raw in raw.get("sources", {}).items():
            manifest.sources[key] = SourceRecord(
                source_id=src_raw["source_id"],
                source_path=src_raw.get("source_path", ""),
                content_hash=src_raw["content_hash"],
                doc_id=src_raw["doc_id"],
                status=src_raw.get("status", "active"),
                chunk_ids=src_raw.get("chunk_ids", []),
                parsed_at=src_raw.get("parsed_at", ""),
            )
        return manifest

    def active_doc_ids(self) -> set[str]:
        return {s.doc_id for s in self.sources.values() if s.status == "active"}

    def active_content_hashes(self) -> set[str]:
        return {s.content_hash for s in self.sources.values() if s.status == "active"}


@dataclass
class ChangeSet:
    """Result of diffing source files against the manifest."""

    to_parse: list[Path]  # new or changed sources
    unchanged: list[str]  # source_ids that are unchanged
    to_delete: list[str]  # source_ids to remove (sync mode only)

    @property
    def is_empty(self) -> bool:
        return not self.to_parse and not self.to_delete


def diff_sources(
    input_dir_sources: list[Path],
    manifest: CorpusManifest,
    *,
    mode: Literal["additive", "sync"] = "additive",
    content_hash_fn=None,
) -> ChangeSet:
    """Compare source files against the manifest to determine what to process.

    ``additive`` mode: only add new/changed sources, never delete.
    ``sync`` mode: also tombstone sources no longer present in input.
    """
    from .pipeline import content_hash as _default_hash

    hash_fn = content_hash_fn or _default_hash
    existing_hashes = manifest.active_content_hashes()

    to_parse: list[Path] = []
    seen_hashes: set[str] = set()
    seen_source_ids: set[str] = set()

    for src in input_dir_sources:
        try:
            h = hash_fn(src)
        except OSError:
            to_parse.append(src)
            continue
        source_id = f"{src.stem}_{h}"
        seen_source_ids.add(source_id)
        seen_hashes.add(h)
        if h in existing_hashes:
            continue  # unchanged
        if source_id in manifest.sources and manifest.sources[source_id].content_hash == h:
            continue  # same file, same content
        to_parse.append(src)

    unchanged = [
        sid for sid, rec in manifest.sources.items()
        if rec.status == "active" and rec.content_hash in seen_hashes
    ]

    to_delete: list[str] = []
    if mode == "sync":
        for sid, rec in manifest.sources.items():
            if rec.status == "active" and rec.content_hash not in seen_hashes:
                to_delete.append(sid)

    return ChangeSet(to_parse=to_parse, unchanged=unchanged, to_delete=to_delete)
