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


def _bibs_from_doc(
    doc: Document,
    doc_citations_map: dict | None = None,
    entries: dict | None = None,
    bibkey_to_doc_id: dict | None = None,
) -> list[dict]:
    """One bib_entry row per CitationEntry, plus bibkey-mapped local_key.

    When *doc_citations_map* / *entries* are present (from citations.json),
    each citation's ord is mapped to its bibkey and stored as `local_key`.
    The bibkey is what skill traversals show in the handle (`doc:ref_...`),
    so populating it makes traverse-references readable.
    """
    out: list[dict] = []
    if not doc.citations:
        return out
    doc_citations_map = doc_citations_map or {}
    entries = entries or {}
    bibkey_to_doc_id = bibkey_to_doc_id or {}
    bibkeys_for_doc = doc_citations_map.get(doc.id, []) or []
    for c in doc.citations:
        bibkey = bibkeys_for_doc[c.ord] if c.ord < len(bibkeys_for_doc) else None
        target_doc = bibkey_to_doc_id.get(bibkey) if bibkey else None
        if target_doc == doc.id:
            target_doc = None
        out.append({
            "bib_id": _bib_id(doc.id, c.ord),
            "ord": c.ord,
            "local_key": bibkey,
            "raw_text": c.raw_text,
            "title": c.title or None,
            "authors": list(c.authors or []),
            "year": c.year,
            "container_title": c.venue or None,
            "publisher": c.publisher or None,
            "doi": c.doi or None,
            "resolution": c.resolution or None,
            "confidence": c.confidence,
            "target_doc_id": target_doc,
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
    store: Store,
    docs: list[Document],
    chunks_by_doc: dict[str, list[Chunk]],
    citation_index: dict | None = None,
) -> None:
    """Upsert documents/chunks/authors/bibs/citations/assets for each doc."""
    citation_index = citation_index or {}
    bibkey_for_doc = (citation_index.get("doc_bibkeys") or {}) if citation_index else {}
    doc_citations_map = (citation_index.get("doc_citations") or {}) if citation_index else {}
    entries = (citation_index.get("entries") or {}) if citation_index else {}
    bibkey_to_doc_id = {bk: did for did, bk in bibkey_for_doc.items()}

    for doc in docs:
        doc_chunks = chunks_by_doc.get(doc.id, [])
        store.upsert_document(doc)
        store.upsert_chunks(doc_chunks)
        store.upsert_document_authors(doc.id, doc.metadata.get("authors") or [])
        store.upsert_authored_edges(doc.id)
        store.upsert_chunk_edges(doc.id)
        bib_rows = _bibs_from_doc(
            doc, doc_citations_map, entries, bibkey_to_doc_id,
        )
        store.upsert_bib_entries(doc.id, bib_rows)
        store.upsert_chunk_citations(
            doc.id, _chunk_citations_from(doc, doc_chunks),
        )
        store.upsert_assets(doc.id, _assets_from_doc(doc))
        store.upsert_chunk_assets(
            doc.id,
            _chunk_asset_mappings(doc) + _equation_chunk_assets(doc, doc_chunks),
        )
    # Cross-doc resolution after every doc has its bibs and self-DOI.
    for doc in docs:
        store.reresolve_inbound(doc.id)
        store.refresh_reference_edges(doc.id)
        # Doc.cites is set by enrichment / by callers that populate the
        # field directly; project those into references too. The bib-side
        # refresh above already DELETEd then INSERTed; we extend with
        # OR IGNORE so overlap with bib-derived edges dedupes.
        cites = [t for t in (doc.cites or []) if t and t != doc.id]
        if cites:
            store.con.executemany(
                "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
                "VALUES ('document', ?, 'references', 'document', ?)",
                [(doc.id, t) for t in cites],
            )


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
    citation_index = _load_citation_index_optional(paths)
    db_path = paths.sqlite_path
    store = Store(db_path)
    try:
        with transaction(store.con):
            _sync_remove_absent_docs(store, {d.id for d in docs})
            project_documents(store, docs, by_doc, citation_index=citation_index)
            if vec is not None and meta is not None:
                project_embeddings(store, vec, meta)
        store.fts_rebuild()
        from .metrics import refresh_cheap_metrics
        refresh_cheap_metrics(store.con)
    finally:
        store.close()
    return db_path


def _load_citation_index_optional(paths: Corpus) -> dict | None:
    """Read citations.json if it exists, else None."""
    if not paths.citation_index_path.exists():
        return None
    try:
        return json.loads(paths.citation_index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sync_remove_absent_docs(store: Store, current_ids: set[str]) -> None:
    """Drop any document rows that aren't in the current ingest's doc set.

    FK cascade clears chunks / bib_entries / assets / chunk_citations /
    chunk_assets. ``graph_edges`` has no FK so we must scrub every row
    whose endpoint refers to a removed entity (doc, chunk, bib_entry,
    asset, or author no longer attached to any surviving doc). Authors
    orphaned by the removal are also deleted, along with their
    embeddings.
    """
    existing = [r[0] for r in store.con.execute("SELECT doc_id FROM documents")]
    to_remove = [d for d in existing if d not in current_ids]
    if not to_remove:
        return
    placeholders = ",".join("?" * len(to_remove))
    # Snapshot the entity ids that are about to disappear via FK cascade.
    chunk_ids = [
        r[0] for r in store.con.execute(
            f"SELECT chunk_id FROM chunks WHERE doc_id IN ({placeholders})", to_remove,
        )
    ]
    bib_ids = [
        r[0] for r in store.con.execute(
            f"SELECT bib_id FROM bib_entries WHERE doc_id IN ({placeholders})", to_remove,
        )
    ]
    asset_ids = [
        r[0] for r in store.con.execute(
            f"SELECT asset_id FROM assets WHERE doc_id IN ({placeholders})", to_remove,
        )
    ]
    author_ids_touched = [
        r[0] for r in store.con.execute(
            f"SELECT DISTINCT author_id FROM document_authors WHERE doc_id IN ({placeholders})",
            to_remove,
        )
    ]
    # Embedding rows for chunks must go before FK cascade clears the chunk row.
    if chunk_ids:
        _delete_in_chunks(
            store.con,
            "DELETE FROM embeddings WHERE node_type='chunk' AND node_id IN ({})",
            chunk_ids,
        )
    store.con.execute(
        f"DELETE FROM documents WHERE doc_id IN ({placeholders})", to_remove,
    )
    # Authors that no longer appear in any document_authors row are now orphans.
    orphan_authors: list[str] = []
    if author_ids_touched:
        rows = _exec_in_chunks(
            store.con,
            "SELECT author_id FROM authors WHERE author_id IN ({}) "
            "AND author_id NOT IN (SELECT DISTINCT author_id FROM document_authors)",
            author_ids_touched,
        )
        orphan_authors = [r[0] for r in rows]
    if orphan_authors:
        _delete_in_chunks(
            store.con,
            "DELETE FROM embeddings WHERE node_type='author' AND node_id IN ({})",
            orphan_authors,
        )
        _delete_in_chunks(
            store.con, "DELETE FROM authors WHERE author_id IN ({})", orphan_authors,
        )
    # Scrub graph_edges referencing any vanished entity (either endpoint).
    removed: list[tuple[str, str]] = (
        [("document", d) for d in to_remove]
        + [("chunk", c) for c in chunk_ids]
        + [("bib_entry", b) for b in bib_ids]
        + [("asset", a) for a in asset_ids]
        + [("author", a) for a in orphan_authors]
    )
    if removed:
        _delete_edges_with_endpoint(store.con, removed)
    # Coauthor edges between two surviving authors can outlive the doc
    # that asserted them: A+B coauthored d1, A also on d2, B also on d3
    # — after deleting d1, the A-B edge has no surviving doc backing it.
    # Both endpoints are canonical so the scrub above can't catch it.
    # Wipe coauthor edges incident to any touched author and rebuild
    # from the surviving document_authors rows.
    surviving_touched = [a for a in author_ids_touched if a not in set(orphan_authors)]
    if surviving_touched:
        _delete_in_chunks(
            store.con,
            "DELETE FROM graph_edges WHERE kind='coauthor' AND src_id IN ({})",
            surviving_touched,
        )
        _delete_in_chunks(
            store.con,
            "DELETE FROM graph_edges WHERE kind='coauthor' AND dst_id IN ({})",
            surviving_touched,
        )
        rebuild_doc_rows = _exec_in_chunks(
            store.con,
            "SELECT DISTINCT doc_id FROM document_authors WHERE author_id IN ({})",
            surviving_touched,
        )
        from .authors import upsert_coauthor_edges as _ucae
        for (doc_id,) in rebuild_doc_rows:
            _ucae(store.con, doc_id)


def _exec_in_chunks(con, sql_template: str, ids: list[str], batch: int = 500):
    """Run ``sql_template`` with an IN-clause expanded over ``ids`` in batches."""
    out: list = []
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        sql = sql_template.format(",".join("?" * len(chunk)))
        out.extend(con.execute(sql, chunk).fetchall())
    return out


def _delete_in_chunks(con, sql_template: str, ids: list[str], batch: int = 500) -> None:
    """DELETE with an IN-clause expanded over ``ids`` in batches."""
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        sql = sql_template.format(",".join("?" * len(chunk)))
        con.execute(sql, chunk)


def _delete_edges_with_endpoint(con, removed: list[tuple[str, str]], batch: int = 250) -> None:
    """Delete graph_edges where either endpoint is in ``removed``."""
    for i in range(0, len(removed), batch):
        chunk = removed[i:i + batch]
        placeholders = ",".join(["(?, ?)"] * len(chunk))
        flat: list[str] = [v for pair in chunk for v in pair]
        con.execute(
            f"DELETE FROM graph_edges WHERE (src_type, src_id) IN ({placeholders}) "
            f"OR (dst_type, dst_id) IN ({placeholders})",
            flat + flat,
        )


def write_doc_metadata_json(store: Store, doc_id: str) -> dict:
    """Snapshot a document row into the JSON sidecar compatibility shape."""
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
