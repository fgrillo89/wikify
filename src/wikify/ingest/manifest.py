"""Corpus manifest: tracks source records for incremental ingest.

Identity model:
  - ``source_id`` is the POSIX relative path from the ingest input root
    (e.g. ``"set1/alpha"`` for ``input/set1/alpha.md``).  Stable across
    content changes; distinguishes same-named files in subdirectories.
  - ``content_hash`` is the sha1[:12] of the file bytes.
  - ``doc_id`` is ``{stem}_{content_hash}`` and changes with content.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal


@dataclass
class SourceRecord:
    """One ingested source file."""

    source_id: str  # stable identity: relative POSIX path without extension
    source_path: str  # original absolute path (informational)
    content_hash: str  # sha1[:12] of file bytes
    doc_id: str  # {stem}_{content_hash}
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

    def active_source_ids(self) -> set[str]:
        return {s.source_id for s in self.sources.values() if s.status == "active"}

    def find_by_source_id(self, source_id: str) -> SourceRecord | None:
        rec = self.sources.get(source_id)
        if rec and rec.status == "active":
            return rec
        return None


@dataclass
class ChangeSet:
    """Result of diffing source files against the manifest."""

    to_parse: list[Path]  # new or changed sources
    unchanged: list[str]  # source_ids that are unchanged
    to_delete: list[str]  # source_ids to remove (sync mode only)
    to_replace: dict[str, str]  # source_id -> old_doc_id (content changed)
    # Map from source absolute path -> source_id, computed during diff.
    path_to_sid: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.to_parse and not self.to_delete


def source_id_for(path: Path, input_root: Path) -> str:
    """Stable source identity: POSIX relative path from input root, no ext.

    ``input/set1/alpha.md`` with root ``input/`` -> ``"set1/alpha"``.
    Falls back to stem if path is not under root.
    """
    try:
        rel = path.resolve().relative_to(input_root.resolve())
    except ValueError:
        return path.stem
    return str(PurePosixPath(rel.with_suffix("")))


def diff_sources(
    input_dir_sources: list[Path],
    manifest: CorpusManifest,
    *,
    input_root: Path,
    mode: Literal["additive", "sync"] = "additive",
    content_hash_fn=None,
) -> ChangeSet:
    """Compare source files against the manifest.

    ``input_root`` is the top-level input directory, used to compute
    stable relative source_ids that distinguish same-named files in
    subdirectories.
    """
    from .pipeline import content_hash as _default_hash

    hash_fn = content_hash_fn or _default_hash

    to_parse: list[Path] = []
    unchanged: list[str] = []
    to_replace: dict[str, str] = {}
    seen_source_ids: set[str] = set()
    path_to_sid: dict[str, str] = {}

    for src in input_dir_sources:
        sid = source_id_for(src, input_root)
        seen_source_ids.add(sid)
        path_to_sid[str(src)] = sid
        try:
            h = hash_fn(src)
        except OSError:
            to_parse.append(src)
            continue

        existing = manifest.find_by_source_id(sid)
        if existing is None:
            to_parse.append(src)
        elif existing.content_hash == h:
            unchanged.append(sid)
        else:
            to_replace[sid] = existing.doc_id
            to_parse.append(src)

    to_delete: list[str] = []
    if mode == "sync":
        for sid in manifest.active_source_ids():
            if sid not in seen_source_ids:
                to_delete.append(sid)

    return ChangeSet(
        to_parse=to_parse,
        unchanged=unchanged,
        to_delete=to_delete,
        to_replace=to_replace,
        path_to_sid=path_to_sid,
    )
