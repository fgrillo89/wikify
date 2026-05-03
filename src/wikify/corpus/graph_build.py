"""Build the KnowledgeGraph from ingest data.

Production ingest writes the graph straight into `wikify.db` (Wave G of
the refresh DAG). This module keeps the legacy
``build_knowledge_graph`` / ``save_knowledge_graph`` /
``load_knowledge_graph`` entry points alive for tests and ad-hoc tools
that hand over Document/Chunk lists rather than ingesting into a real
corpus directory; the implementations now delegate to the SQLite store
and never touch NetworkX.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .graph import AUTHOR, CHUNK, EQUATION, FIGURE, SECTION, SOURCE, KnowledgeGraph
from .store.connection import connect
from .store.kg import SqliteGraphBackend, build_kg_in_memory
from .store.schema import apply_schema

if TYPE_CHECKING:
    from ..models import Chunk, Document
    from .vectors import VectorStore

__all__ = [
    "AUTHOR",
    "CHUNK",
    "EQUATION",
    "FIGURE",
    "SECTION",
    "SOURCE",
    "build_knowledge_graph",
    "load_knowledge_graph",
    "save_knowledge_graph",
]

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _author_key(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\s+", " ", n).strip().rstrip(",.; ")
    n = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", n)
    key = _NORM_RE.sub(" ", n.lower()).strip()
    return re.sub(r"\s+", " ", key)


def build_knowledge_graph(
    docs: list[Document],
    chunks: list[Chunk],
    vectors: VectorStore | None = None,
    citation_index: dict | None = None,
) -> KnowledgeGraph:
    """Build an in-memory SQLite-backed KG from the given documents.

    Used by tests and tooling that don't have a corpus directory. The
    runtime path goes through `read_knowledge_graph(corpus, ...)`.
    """
    return build_kg_in_memory(
        docs, chunks, vectors=vectors, citation_index=citation_index,
    )


def save_knowledge_graph(path: Path, kg: KnowledgeGraph) -> None:
    """Persist *kg* to a fresh SQLite database at *path*.

    Tests serialize a built KG and read it back with
    `load_knowledge_graph`. Production no longer materialises a JSON
    sidecar (the live graph IS `wikify.db`); this helper exists for the
    test surface only.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    target = connect(p)
    try:
        apply_schema(target)
        target.execute("BEGIN")
        try:
            _copy_backend_to_db(kg._backend, target)
            target.execute("COMMIT")
        except BaseException:
            target.execute("ROLLBACK")
            raise
    finally:
        target.close()


def load_knowledge_graph(
    path: Path,
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> KnowledgeGraph:
    """Open a SQLite-backed KG saved with `save_knowledge_graph`."""
    backend = SqliteGraphBackend(Path(path))
    return KnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)


# ---------------------------------------------------------------------------
# Copy in-memory backend -> SQLite file (used by save_knowledge_graph).
# ---------------------------------------------------------------------------


def _copy_backend_to_db(backend, target: sqlite3.Connection) -> None:
    """Copy the rows from `backend.con` into `target`. Both are the same
    schema; this is a straight INSERT-by-SELECT across attached DBs."""
    src = getattr(backend, "con", None)
    if src is None:
        return
    src_path = _connection_filename(src)
    if src_path:
        target.execute("ATTACH DATABASE ? AS src", (src_path,))
        try:
            for table in (
                "documents", "chunks", "authors", "document_authors",
                "bib_entries", "chunk_citations",
                "assets", "chunk_assets",
                "embedding_spaces", "embeddings",
                "graph_edges",
                "node_metrics", "edge_metrics",
                "graph_views", "projection_status",
            ):
                target.execute(f"DELETE FROM {table}")
                target.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
        finally:
            target.execute("DETACH DATABASE src")
    else:
        # Source is :memory: — cannot ATTACH. Stream rows row-by-row.
        for table, cols in _TABLE_COPY_ORDER:
            target.execute(f"DELETE FROM {table}")
            rows = src.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
            if not rows:
                continue
            placeholders = ",".join("?" * len(cols))
            target.executemany(
                f"INSERT INTO {table}({','.join(cols)}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )


def _connection_filename(con: sqlite3.Connection) -> str | None:
    """Return the disk path for a SQLite connection or None for :memory:."""
    row = con.execute("PRAGMA database_list").fetchone()
    # row is (seq, name, file). file is "" for :memory:.
    if not row:
        return None
    file_path = row[2] if len(row) > 2 else None
    return file_path or None


_TABLE_COPY_ORDER: list[tuple[str, list[str]]] = [
    ("documents", [
        "doc_id", "source_path", "source_kind", "doc_type", "title",
        "abstract", "tldr", "authors_json", "year", "container_title",
        "publisher", "doi", "url", "n_chunks", "n_tokens", "metadata_json",
    ]),
    ("authors", ["author_id", "display_name", "metadata_json"]),
    ("chunks", [
        "chunk_id", "doc_id", "ord", "text", "section_path_json",
        "section_type", "char_start", "char_end", "token_count",
        "is_boilerplate", "equation_ids_json", "metadata_json",
    ]),
    ("document_authors", ["doc_id", "author_id", "position", "role"]),
    ("bib_entries", [
        "bib_id", "doc_id", "ord", "local_key", "raw_text", "title",
        "authors_json", "year", "container_title", "publisher", "doi",
        "url", "target_doc_id", "confidence", "resolution", "bib_json",
    ]),
    ("chunk_citations", [
        "chunk_id", "doc_id", "bib_id", "marker_text",
        "char_start", "char_end", "context",
    ]),
    ("assets", [
        "asset_id", "doc_id", "asset_type", "ord", "page",
        "path", "caption", "content", "metadata_json",
    ]),
    ("chunk_assets", ["chunk_id", "asset_id", "relation", "confidence"]),
    ("embedding_spaces", ["space_id", "backend", "model", "dim", "created_at"]),
    ("embeddings", ["space_id", "node_type", "node_id", "vector"]),
    ("graph_edges", [
        "src_type", "src_id", "kind", "dst_type", "dst_id",
        "weight", "ord", "meta_json",
    ]),
    ("graph_views", [
        "graph_name", "description", "node_types_json", "edge_kinds_json",
        "directed", "weighted", "params_json", "updated_at",
    ]),
    ("node_metrics", [
        "graph_name", "node_type", "node_id", "metric",
        "value", "computed_at", "params_json",
    ]),
    ("edge_metrics", [
        "graph_name", "src_type", "src_id", "kind", "dst_type", "dst_id",
        "metric", "value", "computed_at", "params_json",
    ]),
    ("projection_status", [
        "projection", "scope_type", "scope_id", "status",
        "updated_at", "error_json",
    ]),
]
