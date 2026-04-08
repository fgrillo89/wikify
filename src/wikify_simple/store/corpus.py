"""Read/write the on-disk corpus produced by ingest."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Chunk, CorpusGraph, DocImage, Document
from ..paths import CorpusPaths
from .vectors import VectorStore, load_vectors, save_vectors


def write_document(paths: CorpusPaths, doc: Document, markdown: str, chunks: list[Chunk]) -> None:
    paths.ensure()
    (paths.markdown_dir / f"{doc.id}.md").write_text(markdown, encoding="utf-8")
    (paths.chunks_dir / f"{doc.id}.jsonl").write_text(
        "\n".join(json.dumps(_chunk_to_dict(c)) for c in chunks),
        encoding="utf-8",
    )
    (paths.docs_dir / f"{doc.id}.json").write_text(json.dumps(_doc_to_dict(doc)), encoding="utf-8")


def list_documents(paths: CorpusPaths) -> list[Document]:
    out: list[Document] = []
    if not paths.docs_dir.exists():
        return out
    for f in sorted(paths.docs_dir.glob("*.json")):
        out.append(_doc_from_dict(json.loads(f.read_text(encoding="utf-8"))))
    return out


def read_chunks(paths: CorpusPaths, doc_id: str) -> list[Chunk]:
    f = paths.chunks_dir / f"{doc_id}.jsonl"
    if not f.exists():
        return []
    return [
        _chunk_from_dict(json.loads(line))
        for line in f.read_text(encoding="utf-8").splitlines()
        if line
    ]


def all_chunks(paths: CorpusPaths) -> list[Chunk]:
    out: list[Chunk] = []
    for doc in list_documents(paths):
        out.extend(read_chunks(paths, doc.id))
    return out


def write_graph(paths: CorpusPaths, graph: CorpusGraph) -> None:
    paths.graph_path.write_text(
        json.dumps({"nodes": graph.nodes, "edges": graph.edges}),
        encoding="utf-8",
    )


def read_graph(paths: CorpusPaths) -> CorpusGraph:
    data = json.loads(paths.graph_path.read_text(encoding="utf-8"))
    edges = {k: [tuple(e) for e in v] for k, v in data["edges"].items()}
    return CorpusGraph(nodes=data["nodes"], edges=edges)


def write_vector_store(paths: CorpusPaths, store: VectorStore) -> None:
    save_vectors(paths.vectors_path, store)


def read_vector_store(paths: CorpusPaths) -> VectorStore:
    return load_vectors(paths.vectors_path)


def read_doc_images(doc: Document) -> list[DocImage]:
    """Return DocImage records for ``doc`` by loading sidecars from disk.

    The on-disk sidecars in ``doc.image_dir`` are the source of truth,
    so this function ignores ``doc.images`` and rebuilds the list from
    the JSON sidecars written by ``save_doc_images``.
    """
    from ..ingest.images import load_sidecars

    return load_sidecars(Path(doc.image_dir))


# --- serialisation helpers -----------------------------------------------


def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "id": c.id,
        "doc_id": c.doc_id,
        "ord": c.ord,
        "text": c.text,
        "char_span": list(c.char_span),
        "section_path": c.section_path,
    }


def _chunk_from_dict(d: dict) -> Chunk:
    return Chunk(
        id=d["id"],
        doc_id=d["doc_id"],
        ord=d["ord"],
        text=d["text"],
        char_span=tuple(d["char_span"]),
        section_path=d["section_path"],
    )


def _doc_to_dict(doc: Document) -> dict:
    return {
        "id": doc.id,
        "source_path": doc.source_path,
        "kind": doc.kind,
        "title": doc.title,
        "metadata": doc.metadata,
        "markdown_path": doc.markdown_path,
        "image_dir": doc.image_dir,
        "sections": [
            {"path": s.path, "chunk_ids": s.chunk_ids, "summary": s.summary} for s in doc.sections
        ],
        "images": [_image_to_dict(i) for i in doc.images],
        "abstract": doc.abstract,
        "tldr": doc.tldr,
        "n_chunks": doc.n_chunks,
        "n_tokens": doc.n_tokens,
    }


def _image_to_dict(im: DocImage) -> dict:
    return {
        "id": im.id,
        "path": im.path,
        "caption": im.caption,
        "alt_text": im.alt_text,
        "page": im.page,
        "near_chunk_ids": im.near_chunk_ids,
    }


def _doc_from_dict(d: dict) -> Document:
    from ..models import DocSection

    return Document(
        id=d["id"],
        source_path=d["source_path"],
        kind=d["kind"],
        title=d["title"],
        metadata=d.get("metadata", {}),
        markdown_path=d["markdown_path"],
        image_dir=d["image_dir"],
        sections=[
            DocSection(path=s["path"], chunk_ids=s["chunk_ids"], summary=s.get("summary", ""))
            for s in d.get("sections", [])
        ],
        images=[
            DocImage(
                id=i["id"],
                path=i["path"],
                caption=i.get("caption", ""),
                alt_text=i.get("alt_text", ""),
                page=i.get("page"),
                near_chunk_ids=i.get("near_chunk_ids", []),
            )
            for i in d.get("images", [])
        ],
        abstract=d.get("abstract", ""),
        tldr=d.get("tldr", ""),
        n_chunks=d.get("n_chunks", 0),
        n_tokens=d.get("n_tokens", 0),
    )
