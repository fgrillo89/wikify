"""Project ingest in-memory artefacts into the SQLite query store.

Bridge between the existing on-disk corpus shape (Document + Chunk +
VectorStore + images.json + equations.json + citations) and the new
canonical tables. Used by `ingest.dag._refresh_sqlite_store` and by any
ad-hoc rebuild that wants to materialise `wikify.db` from corpus state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import Store, transaction

if TYPE_CHECKING:
    from ...api import Corpus
    from ...models import Chunk, Document
    from ..vectors import VectorStore
    from ..vectors_meta import VectorsMeta


_MARKER_RE = re.compile(r"\[(\d+)\]")


def space_id_for(meta: VectorsMeta) -> str:
    """Stable embedding-space id from the vectors fingerprint."""
    if meta.backend == "fastembed":
        return f"fastembed:{(meta.model or 'unknown')}:{meta.dim}"
    return f"{meta.backend}:{meta.dim}"


def _bib_id(doc_id: str, ord_i: int) -> str:
    return f"{doc_id}::bib:{ord_i:04d}"


def _bibs_from_doc(doc: Document) -> list[dict]:
    """One bib_entry row per CitationEntry on the document."""
    out: list[dict] = []
    if not doc.citations:
        return out
    for c in doc.citations:
        out.append({
            "bib_id": _bib_id(doc.id, c.ord),
            "ord": c.ord,
            "raw_text": c.raw_text,
            "title": c.title or None,
            "authors": list(c.authors or []),
            "year": c.year,
            "container_title": c.venue or None,
            "publisher": c.publisher or None,
            "doi": c.doi or None,
            "resolution": c.resolution or None,
            "confidence": c.confidence,
        })
    return out


def _chunk_citations_from(doc: Document, chunks: list[Chunk]) -> list[dict]:
    """Detect [N] markers in chunk text and link them to bib_entry by ord."""
    if not doc.citations:
        return []
    by_ord = {c.ord: c for c in doc.citations}
    rows: list[dict] = []
    for ck in chunks:
        for m in _MARKER_RE.finditer(ck.text):
            try:
                ord_n = int(m.group(1))
            except ValueError:
                continue
            if ord_n not in by_ord:
                continue
            rows.append({
                "chunk_id": ck.id,
                "bib_id": _bib_id(doc.id, ord_n),
                "marker_text": m.group(0),
                "char_start": m.start(),
                "char_end": m.end(),
            })
    return rows


def _assets_from_doc(doc: Document) -> list[dict]:
    """Figures + equations as `assets` rows."""
    out: list[dict] = []
    for img in doc.images or []:
        out.append({
            "id": img.id,
            "type": "figure",
            "page": img.page,
            "path": img.path,
            "caption": img.caption,
        })
    for ord_i, eq in enumerate(doc.equations or []):
        if not isinstance(eq, dict):
            continue
        eq_id = eq.get("id") or f"{doc.id}/eq_{ord_i:03d}"
        out.append({
            "id": eq_id,
            "type": "equation",
            "ord": ord_i,
            "content": eq.get("latex") or eq.get("text"),
            "page": eq.get("page"),
            "caption": eq.get("label"),
        })
    return out


def _chunk_asset_mappings(doc: Document) -> list[dict]:
    """Image -> chunk near-edges from DocImage.near_chunk_ids; equations
    are linked through the chunk's `equation_ids` field (added below)."""
    out: list[dict] = []
    for img in doc.images or []:
        for cid in img.near_chunk_ids or []:
            out.append({"chunk_id": cid, "asset_id": img.id, "relation": "near"})
    return out


def _equation_chunk_assets(doc: Document, chunks: list[Chunk]) -> list[dict]:
    out: list[dict] = []
    for ck in chunks:
        for eq_id in ck.equation_ids or []:
            out.append({"chunk_id": ck.id, "asset_id": eq_id, "relation": "contains"})
    return out


def project_documents(
    store: Store, docs: list[Document], chunks_by_doc: dict[str, list[Chunk]],
) -> None:
    """Upsert documents/chunks/authors/bibs/citations/assets for each doc."""
    for doc in docs:
        doc_chunks = chunks_by_doc.get(doc.id, [])
        store.upsert_document(doc)
        store.upsert_chunks(doc_chunks)
        store.upsert_document_authors(doc.id, doc.metadata.get("authors") or [])
        store.upsert_authored_edges(doc.id)
        store.upsert_chunk_edges(doc.id)
        store.upsert_bib_entries(doc.id, _bibs_from_doc(doc))
        store.upsert_chunk_citations(doc.id, _chunk_citations_from(doc, doc_chunks))
        store.upsert_assets(doc.id, _assets_from_doc(doc))
        store.upsert_chunk_assets(
            doc.id,
            _chunk_asset_mappings(doc) + _equation_chunk_assets(doc, doc_chunks),
        )
    # Cross-doc resolution after every doc has its bibs and self-DOI.
    for doc in docs:
        store.reresolve_inbound(doc.id)
        store.refresh_reference_edges(doc.id)


def project_embeddings(store: Store, vec: VectorStore, meta: VectorsMeta) -> None:
    space = space_id_for(meta)
    store.upsert_embedding_space(space, meta.backend, meta.model, int(meta.dim or 0))
    items: list[tuple[str, str, np.ndarray]] = []
    for i, cid in enumerate(vec.ids):
        items.append(("chunk", cid, vec.matrix[i]))
    if items:
        store.upsert_embeddings(space, items)


def write_corpus(
    paths: Corpus,
    docs: list[Document],
    chunks: list[Chunk],
    vec: VectorStore | None,
    meta: VectorsMeta | None,
) -> Path:
    """Top-level dual-write entry point. Returns the wikify.db path."""
    by_doc: dict[str, list[Chunk]] = {}
    for ck in chunks:
        by_doc.setdefault(ck.doc_id, []).append(ck)
    for v in by_doc.values():
        v.sort(key=lambda c: c.ord)
    db_path = paths.sqlite_path
    store = Store(db_path)
    try:
        with transaction(store.con):
            project_documents(store, docs, by_doc)
            if vec is not None and meta is not None:
                project_embeddings(store, vec, meta)
        store.fts_rebuild()
        from .metrics import refresh_cheap_metrics
        refresh_cheap_metrics(store.con)
    finally:
        store.close()
    return db_path


def write_doc_metadata_json(store: Store, doc_id: str) -> dict:
    """Snapshot a document row into the same dict shape the legacy json
    files use; helpful for parity tests in Phase 2."""
    row = store.get_document(doc_id)
    if not row:
        return {}
    out = dict(row)
    out["authors"] = json.loads(out.pop("authors_json") or "[]")
    if out.get("metadata_json"):
        meta = json.loads(out["metadata_json"])
        out["metadata"] = meta
    out.pop("metadata_json", None)
    return out
