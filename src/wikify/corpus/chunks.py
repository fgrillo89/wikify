"""Read/write the on-disk corpus produced by ingest."""

import json
import os
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wikify.citations.models import CitationEntry

from ..api import Corpus
from ..models import Chunk, DocImage, DocSection, Document
from .vectors import VectorStore


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".corpus-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_document_markdown(paths: Corpus, doc_id: str, markdown: str) -> None:
    """Persist the parsed markdown for *doc_id*.

    Document/chunk rows live in `wikify.db`; this writer only handles the
    markdown sidecar that downstream readers (rechunker, body-of-text
    queries) load back from disk.
    """
    paths.ensure()
    atomic_write_text(paths.markdown_dir / f"{doc_id}.md", markdown)


def list_documents(paths: Corpus) -> list[Document]:
    con = _connect_sqlite(paths)
    if con is None:
        return []
    try:
        rows = con.execute("SELECT * FROM documents ORDER BY doc_id").fetchall()
        return [_doc_from_sqlite_row(paths, con, dict(r)) for r in rows]
    finally:
        con.close()


def read_document(paths: Corpus, doc_id: str) -> Document | None:
    """Load a single Document by id, or None if absent."""
    con = _connect_sqlite(paths)
    if con is None:
        return None
    try:
        row = con.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return _doc_from_sqlite_row(paths, con, dict(row))
    finally:
        con.close()


def read_chunks(paths: Corpus, doc_id: str) -> list[Chunk]:
    con = _connect_sqlite(paths)
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,),
        ).fetchall()
        return [_chunk_from_sqlite_row(dict(r)) for r in rows]
    finally:
        con.close()


def all_chunks(paths: Corpus) -> list[Chunk]:
    con = _connect_sqlite(paths)
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT * FROM chunks ORDER BY doc_id, ord",
        ).fetchall()
        return [_chunk_from_sqlite_row(dict(r)) for r in rows]
    finally:
        con.close()


def read_chunks_by_id(
    corpus: Corpus,
    chunk_ids: Sequence[str],
    limit: int | None = None,
) -> list[Chunk]:
    """Look up chunks by id in the SQLite store.

    Preserves the requested order and stops after *limit* returned
    chunks when provided. Unknown ids are dropped.
    """
    if not chunk_ids:
        return []
    con = _connect_sqlite(corpus)
    if con is None:
        return []
    try:
        wanted = list(dict.fromkeys(chunk_ids))
        placeholders = ",".join("?" * len(wanted))
        rows = con.execute(
            f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
            wanted,
        ).fetchall()
        by_id = {
            str(r["chunk_id"]): _chunk_from_sqlite_row(dict(r))
            for r in rows
        }
        cap = limit if limit is not None else len(chunk_ids)
        out: list[Chunk] = []
        for cid in chunk_ids:
            chunk = by_id.get(cid)
            if chunk is not None:
                out.append(chunk)
                if len(out) >= cap:
                    break
        return out
    finally:
        con.close()


def _connect_sqlite(paths: Corpus) -> sqlite3.Connection | None:
    db = paths.sqlite_path
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def _json_obj(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _json_list(raw: Any) -> list:
    if not raw:
        return []
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _chunk_from_sqlite_row(row: dict[str, Any]) -> Chunk:
    return Chunk(
        id=str(row["chunk_id"]),
        doc_id=str(row["doc_id"]),
        ord=int(row["ord"]),
        text=str(row["text"] or ""),
        char_span=(
            int(row["char_start"] or 0),
            int(row["char_end"] or 0),
        ),
        section_path=[str(p) for p in _json_list(row.get("section_path_json"))],
        section_type=str(row.get("section_type") or "body"),
        equation_ids=[str(e) for e in _json_list(row.get("equation_ids_json"))],
        is_boilerplate=bool(row.get("is_boilerplate")),
    )


def _sections_from_chunk_rows(rows: list[dict[str, Any]]) -> list[DocSection]:
    sections: list[DocSection] = []
    for row in rows:
        path = [str(p) for p in _json_list(row.get("section_path_json"))]
        if not path:
            path = ["body"]
        chunk_id = str(row["chunk_id"])
        if sections and sections[-1].path == path:
            sections[-1].chunk_ids.append(chunk_id)
        else:
            sections.append(DocSection(path=path, chunk_ids=[chunk_id]))
    return sections


def _citations_from_sqlite(
    con: sqlite3.Connection,
    doc_id: str,
) -> list[CitationEntry]:
    rows = con.execute(
        "SELECT * FROM bib_entries WHERE doc_id = ? ORDER BY ord", (doc_id,),
    ).fetchall()
    out: list[CitationEntry] = []
    for r in rows:
        row = dict(r)
        bib = _json_obj(row.get("bib_json"))
        authors = _json_list(row.get("authors_json"))
        raw_authors = bib.get("author_last_names") or []
        out.append(CitationEntry(
            ord=int(row.get("ord") or 0),
            raw_text=str(row.get("raw_text") or ""),
            title=str(row.get("title") or ""),
            authors=[str(a) for a in authors],
            author_last_names=[str(a) for a in raw_authors],
            year=row.get("year"),
            doi=str(row.get("doi") or ""),
            venue=str(row.get("container_title") or ""),
            publisher=str(row.get("publisher") or ""),
            resolution=str(row.get("resolution") or ""),
            confidence=float(row.get("confidence") or 0.0),
        ))
    return out


def _assets_from_sqlite(
    con: sqlite3.Connection,
    doc_id: str,
) -> tuple[list[DocImage], list[dict]]:
    rows = con.execute(
        "SELECT * FROM assets WHERE doc_id = ? ORDER BY asset_type, ord", (doc_id,),
    ).fetchall()
    near_rows = con.execute(
        "SELECT ca.asset_id, ca.chunk_id FROM chunk_assets ca "
        "JOIN chunks c ON c.chunk_id = ca.chunk_id "
        "WHERE c.doc_id = ? AND ca.relation = 'near' "
        "ORDER BY ca.asset_id, c.ord",
        (doc_id,),
    ).fetchall()
    near_by_asset: dict[str, list[str]] = {}
    for r in near_rows:
        near_by_asset.setdefault(str(r["asset_id"]), []).append(str(r["chunk_id"]))

    images: list[DocImage] = []
    equations: list[dict] = []
    for r in rows:
        row = dict(r)
        asset_type = str(row.get("asset_type") or "")
        meta = _json_obj(row.get("metadata_json"))
        if asset_type in {"figure", "image", "table", "scheme"}:
            images.append(DocImage(
                id=str(row["asset_id"]),
                path=str(row.get("path") or ""),
                caption=str(row.get("caption") or ""),
                alt_text=str(meta.get("alt_text") or ""),
                page=row.get("page"),
                near_chunk_ids=near_by_asset.get(str(row["asset_id"]), []),
            ))
        elif asset_type == "equation":
            eq = dict(meta)
            eq.setdefault("id", str(row["asset_id"]))
            eq.setdefault("latex", str(row.get("content") or ""))
            eq.setdefault("type", "equation")
            if row.get("caption"):
                eq.setdefault("label", str(row["caption"]))
            if row.get("page") is not None:
                eq.setdefault("page", row["page"])
            equations.append(eq)
    return images, equations


def _edge_targets(
    con: sqlite3.Connection,
    doc_id: str,
    *,
    kind: str,
) -> list[str]:
    return [
        str(r["dst_id"]) for r in con.execute(
            "SELECT dst_id FROM graph_edges "
            "WHERE src_type='document' AND src_id=? AND kind=? "
            "AND dst_type='document' ORDER BY ord, dst_id",
            (doc_id, kind),
        )
    ]


def _doc_from_sqlite_row(
    paths: Corpus,
    con: sqlite3.Connection,
    row: dict[str, Any],
) -> Document:
    doc_id = str(row["doc_id"])
    metadata = _json_obj(row.get("metadata_json"))
    metadata.setdefault("authors", _json_list(row.get("authors_json")))
    for source, key in (
        ("year", "year"),
        ("container_title", "venue"),
        ("publisher", "publisher"),
        ("doi", "doi"),
        ("url", "url"),
        ("doc_type", "doc_type"),
    ):
        if row.get(source) not in (None, ""):
            metadata.setdefault(key, row[source])

    chunk_rows = [
        dict(r) for r in con.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ord", (doc_id,),
        )
    ]
    images, equations = _assets_from_sqlite(con, doc_id)
    image_dir = paths.images_dir / doc_id
    if images and images[0].path:
        image_dir = Path(images[0].path).parent
    return Document(
        id=doc_id,
        source_path=str(row.get("source_path") or ""),
        kind=str(row.get("source_kind") or "md"),  # type: ignore[arg-type]
        title=str(row.get("title") or doc_id),
        metadata=metadata,
        markdown_path=str(paths.markdown_dir / f"{doc_id}.md"),
        image_dir=str(image_dir),
        sections=_sections_from_chunk_rows(chunk_rows),
        images=images,
        abstract=str(row.get("abstract") or ""),
        tldr=str(row.get("tldr") or ""),
        n_chunks=int(row.get("n_chunks") or len(chunk_rows)),
        n_tokens=int(row.get("n_tokens") or 0),
        citations=_citations_from_sqlite(con, doc_id),
        equations=equations,
        similar_to=_edge_targets(con, doc_id, kind="similar"),
        cites=_edge_targets(con, doc_id, kind="references"),
        cites_same=_edge_targets(con, doc_id, kind="cites_same"),
    )


def read_vector_store(paths: Corpus) -> VectorStore:
    """Load chunk embeddings as a `VectorStore` from `wikify.db`.

    Returns an empty store when the SQLite store is absent so hand-built
    fixtures and pre-build paths don't blow up.
    """
    if paths.sqlite_path.exists():
        return _vector_store_from_sqlite(paths.sqlite_path)
    import numpy as np
    return VectorStore(ids=[], matrix=np.zeros((0, 1), dtype="float32"))


def _vector_store_from_sqlite(sqlite_path) -> VectorStore:
    import numpy as np

    from .store.connection import connect

    con = connect(sqlite_path)
    try:
        space = con.execute(
            "SELECT space_id, dim FROM embedding_spaces "
            "ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        if not space:
            return VectorStore(ids=[], matrix=np.zeros((0, 1), dtype="float32"))
        rows = con.execute(
            "SELECT node_id, vector FROM embeddings "
            "WHERE space_id = ? AND node_type = 'chunk' ORDER BY node_id",
            (space["space_id"],),
        ).fetchall()
        if not rows:
            return VectorStore(
                ids=[],
                matrix=np.zeros((0, int(space["dim"])), dtype="float32"),
            )
        ids = [r[0] for r in rows]
        matrix = np.frombuffer(
            b"".join(r[1] for r in rows), dtype="float32",
        ).reshape(len(rows), int(space["dim"]))
        return VectorStore(ids=ids, matrix=matrix)
    finally:
        con.close()


def read_knowledge_graph(
    paths: Corpus,
    vectors: object | None = None,
    embed_fn: object | None = None,
) -> object:
    """Open the SQLite-backed KnowledgeGraph for `paths`.

    Returns an empty KG if `wikify.db` does not yet exist (matches the
    legacy contract for hand-built fixtures).
    """
    from wikify.corpus.graph import KnowledgeGraph
    from wikify.corpus.store.kg import SqliteGraphBackend

    if not paths.sqlite_path.exists():
        # Empty backend: no SQLite yet (e.g. legacy fixture or pre-build).
        backend = _empty_sqlite_backend()
    else:
        backend = SqliteGraphBackend(paths.sqlite_path)
    return KnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)


def _empty_sqlite_backend():
    """Stand-in for an empty corpus KG; used by callers that pre-build a
    KnowledgeGraph manually (tests) or by paths where wikify.db is absent."""
    from wikify.corpus.store.connection import connect
    from wikify.corpus.store.kg import SqliteGraphBackend
    from wikify.corpus.store.schema import apply_schema

    con = connect(":memory:")
    apply_schema(con)
    return SqliteGraphBackend(con)


def read_doc_images(doc: Document) -> list[DocImage]:
    """Return DocImage records for ``doc`` by loading sidecars from disk.

    The on-disk sidecars in ``doc.image_dir`` are the source of truth,
    so this function ignores ``doc.images`` and rebuilds the list from
    the JSON sidecars written by ``save_doc_images``.
    """
    from ..ingest.images import load_sidecars

    return load_sidecars(Path(doc.image_dir))
