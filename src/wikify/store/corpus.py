"""Read/write the on-disk corpus produced by ingest."""

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..citestore.models import CitationEntry
from ..models import Chunk, CorpusGraph, DocImage, Document
from ..paths import CorpusPaths
from .vectors import VectorStore, load_vectors, save_vectors


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


def write_document(paths: CorpusPaths, doc: Document, markdown: str, chunks: list[Chunk]) -> None:
    paths.ensure()
    atomic_write_text(
        paths.markdown_dir / f"{doc.id}.md", markdown,
    )
    atomic_write_text(
        paths.chunks_dir / f"{doc.id}.jsonl",
        "\n".join(json.dumps(_chunk_to_dict(c)) for c in chunks),
    )
    atomic_write_text(
        paths.docs_dir / f"{doc.id}.json", json.dumps(_doc_to_dict(doc)),
    )


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


def read_chunks_by_id(
    corpus: CorpusPaths,
    chunk_ids: Sequence[str],
    limit: int | None = None,
) -> list[Chunk]:
    """Look up chunks by id using the real ``chunks/{doc_id}.jsonl`` layout.

    Scans JSONL files to find the requested chunk ids. Preserves the
    requested order. Returns only chunks that exist.  Stops after
    *limit* returned chunks (in requested order) when provided.
    """
    wanted = set(chunk_ids)
    if not wanted:
        return []

    # Scan all JSONL files to build a complete map of wanted chunks.
    found: dict[str, Chunk] = {}
    if not corpus.chunks_dir.exists():
        return []
    for f in corpus.chunks_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            d = json.loads(line)
            cid = d.get("id", "")
            if cid in wanted:
                found[cid] = _chunk_from_dict(d)
                if len(found) == len(wanted):
                    break
        if len(found) == len(wanted):
            break

    # Return in requested order, capped by limit.
    cap = limit if limit is not None else len(chunk_ids)
    result: list[Chunk] = []
    for cid in chunk_ids:
        if cid in found:
            result.append(found[cid])
            if len(result) >= cap:
                break
    return result


def write_graph(paths: CorpusPaths, graph: CorpusGraph) -> None:
    atomic_write_text(
        paths.graph_path,
        json.dumps({"nodes": graph.nodes, "edges": graph.edges}),
    )


def read_graph(paths: CorpusPaths) -> CorpusGraph:
    data = json.loads(paths.graph_path.read_text(encoding="utf-8"))
    edges = {k: [tuple(e) for e in v] for k, v in data["edges"].items()}
    return CorpusGraph(nodes=data["nodes"], edges=edges)


def write_vector_store(paths: CorpusPaths, store: VectorStore) -> None:
    save_vectors(paths.vectors_path, store)


def read_vector_store(paths: CorpusPaths) -> VectorStore:
    return load_vectors(paths.vectors_path)


def write_knowledge_graph(paths: CorpusPaths, kg: object) -> None:
    """Persist a KnowledgeGraph to knowledge_graph.json."""
    from ..citestore.graph_build import save_knowledge_graph

    save_knowledge_graph(paths.knowledge_graph_path, kg)


def read_knowledge_graph(paths: CorpusPaths, vectors: object | None = None) -> object:
    """Load a KnowledgeGraph from knowledge_graph.json."""
    from ..citestore.graph_build import load_knowledge_graph

    return load_knowledge_graph(paths.knowledge_graph_path, vectors=vectors)


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
        "section_type": c.section_type,
        "equation_ids": list(c.equation_ids or []),
    }


def _chunk_from_dict(d: dict) -> Chunk:
    return Chunk(
        id=d["id"],
        doc_id=d["doc_id"],
        ord=d["ord"],
        text=d["text"],
        char_span=tuple(d["char_span"]),
        section_path=d["section_path"],
        section_type=d.get("section_type", "body"),
        equation_ids=list(d.get("equation_ids") or []),
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
        "citations": [c.to_dict() for c in doc.citations] if doc.citations else [],
        "equations": list(doc.equations or []),
        "figure_refs": list(doc.figure_refs or []),
        "similar_to": list(doc.similar_to or []),
        "cites": list(doc.cites or []),
        "cites_same": list(doc.cites_same or []),
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
        citations=[CitationEntry.from_dict(c) for c in (d.get("citations") or [])],
        equations=list(d.get("equations") or []),
        figure_refs=list(d.get("figure_refs") or []),
        similar_to=list(d.get("similar_to") or []),
        cites=list(d.get("cites") or []),
        cites_same=list(d.get("cites_same") or []),
    )
