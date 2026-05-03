"""SQLite query store for the corpus.

`wikify.db` lives at `<corpus_root>/wikify.db`. One file holds canonical
entity rows (documents/chunks/authors/bib_entries/assets), FTS5 indexes,
embeddings, the graph_edges table, and the metric projections.

`Store` is the public entry point; it composes the per-area helpers into
one surface so callers don't need to know how the package is split.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from ...models import Chunk, Document
from . import assets as _assets
from . import authors as _authors
from . import bib as _bib
from . import documents as _docs
from . import fts as _fts
from . import vectors as _vectors
from .connection import connect, transaction
from .graph import Edge, GraphStore
from .schema import SCHEMA_VERSION, apply_schema
from .vectors import VectorIndex

__all__ = [
    "SCHEMA_VERSION", "Edge", "GraphStore", "Store", "VectorIndex",
    "apply_schema", "connect", "transaction",
]


class Store:
    """One-stop facade over the SQLite corpus query store."""

    def __init__(self, path: str | Path):
        self.path = Path(path) if path != ":memory:" else path
        self.con: sqlite3.Connection = connect(path)
        apply_schema(self.con)
        self._graph: GraphStore | None = None
        self._vector_indexes: dict[tuple[str, str], VectorIndex] = {}

    # ------------------------------------------------------------------
    # life-cycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # documents / chunks
    # ------------------------------------------------------------------

    def upsert_document(self, doc: Document) -> None:
        _docs.upsert_document(self.con, doc)

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        return _docs.get_document(self.con, doc_id)

    def list_documents(self) -> list[dict[str, Any]]:
        return _docs.list_documents(self.con)

    def delete_document(self, doc_id: str) -> None:
        _docs.delete_document(self.con, doc_id)
        self._invalidate_vectors()

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        _docs.upsert_chunks(self.con, chunks)

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        return _docs.get_chunks(self.con, doc_id)

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        return _docs.get_chunk(self.con, chunk_id)

    def all_chunks(self) -> list[dict[str, Any]]:
        return _docs.all_chunks(self.con)

    # ------------------------------------------------------------------
    # authors
    # ------------------------------------------------------------------

    def upsert_document_authors(self, doc_id: str, authors: list[str]) -> list[str]:
        ids = _authors.upsert_document_authors(self.con, doc_id, authors)
        _authors.upsert_coauthor_edges(self.con, doc_id)
        return ids

    def get_authors(self, doc_id: str) -> list[dict[str, Any]]:
        return _authors.get_authors_for_document(self.con, doc_id)

    def get_documents_for_author(self, author_id: str) -> list[str]:
        return _authors.get_documents_for_author(self.con, author_id)

    # ------------------------------------------------------------------
    # bib / citations
    # ------------------------------------------------------------------

    def upsert_bib_entries(self, doc_id: str, entries: list[dict[str, Any]]) -> None:
        _bib.upsert_bib_entries(self.con, doc_id, entries)

    def get_bib_entries(self, doc_id: str) -> list[dict[str, Any]]:
        return _bib.get_bib_entries(self.con, doc_id)

    def upsert_chunk_citations(self, doc_id: str, citations: list[dict[str, Any]]) -> None:
        _bib.upsert_chunk_citations(self.con, doc_id, citations)
        _bib.upsert_chunk_cites_edges(self.con, doc_id)

    def reresolve_inbound(self, new_doc_id: str) -> int:
        return _bib.reresolve_inbound(self.con, new_doc_id)

    def refresh_reference_edges(self, doc_id: str) -> None:
        _bib.upsert_bib_resolved_edges(self.con, doc_id)
        _bib.upsert_reference_edges(self.con, doc_id)

    def export_bibtex(self, kind: str) -> str:
        return _bib.export_bibtex(self.con, kind)  # type: ignore[arg-type]

    def import_citestore_facts(self, citestore_db: str) -> int:
        return _bib.import_citestore_facts(self.con, citestore_db)

    # ------------------------------------------------------------------
    # assets
    # ------------------------------------------------------------------

    def upsert_assets(self, doc_id: str, assets: list[dict[str, Any]]) -> None:
        _assets.upsert_assets(self.con, doc_id, assets)

    def get_assets(self, doc_id: str) -> list[dict[str, Any]]:
        return _assets.get_assets(self.con, doc_id)

    def upsert_chunk_assets(self, doc_id: str, mappings: list[dict[str, Any]]) -> None:
        _assets.upsert_chunk_assets(self.con, doc_id, mappings)
        _assets.upsert_asset_edges(self.con, doc_id)

    # ------------------------------------------------------------------
    # graph
    # ------------------------------------------------------------------

    @property
    def graph(self) -> GraphStore:
        if self._graph is None:
            self._graph = GraphStore(self.con)
        return self._graph

    def upsert_chunk_edges(self, doc_id: str) -> None:
        """Insert document -> chunk has_chunk edges for *doc_id*."""
        self.con.execute(
            "DELETE FROM graph_edges WHERE src_type='document' AND src_id=? AND kind='has_chunk'",
            (doc_id,),
        )
        self.con.executemany(
            "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id, ord) "
            "VALUES ('document', ?, 'has_chunk', 'chunk', ?, ?)",
            [
                (doc_id, r[0], r[1])
                for r in self.con.execute(
                    "SELECT chunk_id, ord FROM chunks WHERE doc_id = ? ORDER BY ord",
                    (doc_id,),
                )
            ],
        )

    def upsert_authored_edges(self, doc_id: str) -> None:
        self.con.execute(
            "DELETE FROM graph_edges WHERE src_type='document' AND src_id=? AND kind='authored_by'",
            (doc_id,),
        )
        self.con.executemany(
            "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id, ord) "
            "VALUES ('document', ?, 'authored_by', 'author', ?, ?)",
            [
                (doc_id, r[0], r[1])
                for r in self.con.execute(
                    "SELECT author_id, position FROM document_authors "
                    "WHERE doc_id = ? ORDER BY position",
                    (doc_id,),
                )
            ],
        )

    # ------------------------------------------------------------------
    # FTS / vectors / hybrid
    # ------------------------------------------------------------------

    def fts_rebuild(self) -> None:
        _fts.fts_rebuild(self.con)

    def fts_refresh_chunk(self, chunk_id: str) -> None:
        _fts.fts_refresh_chunk(self.con, chunk_id)

    def fts_refresh_document(self, doc_id: str) -> None:
        _fts.fts_refresh_document(self.con, doc_id)

    def search_chunks_bm25(
        self, query: str, top_k: int = 10, *, doc_id: str | None = None,
    ) -> list[tuple[str, float]]:
        return _fts.search_chunks_bm25(self.con, query, top_k, doc_id=doc_id)

    def search_documents_bm25(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        return _fts.search_documents_bm25(self.con, query, top_k)

    def search_hybrid(
        self,
        query: str,
        query_vec: np.ndarray | None = None,
        *,
        space_id: str | None = None,
        top_k: int = 10,
        pool: int = 200,
        k_rrf: int = _fts.RRF_K_DEFAULT,
    ) -> list[tuple[str, float]]:
        vi: VectorIndex | None = None
        if query_vec is not None and space_id is not None:
            vi = self.vector_index(space_id)
        return _fts.hybrid_search_chunks(
            self.con, query, query_vec,
            vector_index=vi, top_k=top_k, pool=pool, k_rrf=k_rrf,
        )

    def upsert_embedding_space(
        self, space_id: str, backend: str, model: str | None, dim: int,
    ) -> None:
        _vectors.upsert_embedding_space(self.con, space_id, backend, model, dim)

    def upsert_embeddings(
        self,
        space_id: str,
        items: Iterable[tuple[str, str, np.ndarray]],
    ) -> None:
        _vectors.upsert_embeddings(self.con, space_id, list(items))
        self._invalidate_vectors()

    def delete_embeddings_for_node(self, space_id: str, node_type: str, node_id: str) -> None:
        _vectors.delete_embeddings_for_node(self.con, space_id, node_type, node_id)
        self._invalidate_vectors()

    def vector_index(self, space_id: str, node_type: str = "chunk") -> VectorIndex:
        key = (space_id, node_type)
        if key not in self._vector_indexes:
            self._vector_indexes[key] = VectorIndex(self.con, space_id, node_type)
        return self._vector_indexes[key]

    def _invalidate_vectors(self) -> None:
        for vi in self._vector_indexes.values():
            vi.invalidate()
