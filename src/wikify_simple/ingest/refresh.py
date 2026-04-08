"""Single entry point: ``ingest_corpus(input_dir, output_dir)``.

Walks an input directory, parses every supported file, chunks it, embeds
chunks + image captions, materialises the corpus graph, extracts the
topic vocabulary, and writes everything under ``output_dir``. Idempotent
modulo file content (file hash → doc id).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import Document
from ..paths import CorpusPaths
from ..store.corpus import (
    write_document,
    write_graph,
    write_vector_store,
)
from ..store.vectors import VectorStore
from .chunker import chunk_document
from .corpus_graph import build_corpus_graph
from .embedder import embed_texts
from .images import caption_chunks_for, save_doc_images
from .parsers.registry import parse_file
from .topics import extract_topics, write_topics


def ingest_corpus(input_dir: Path, output_dir: Path) -> CorpusPaths:
    paths = CorpusPaths(root=output_dir)
    paths.ensure()

    docs: list[Document] = []
    all_chunks_list = []
    docs_chunks_pairs = []
    declared: dict[str, list[str]] = {}

    for src in sorted(_iter_sources(input_dir)):
        kind, parsed = parse_file(src)
        doc_id = _doc_id_for(src)
        chunks = chunk_document(doc_id, parsed.markdown, parsed.sections)
        chunks += caption_chunks_for(doc_id, parsed.images, ord_offset=len(chunks))

        markdown_path = str(paths.markdown_dir / f"{doc_id}.md")
        image_dir = str(paths.images_dir / doc_id)

        raw_images = parsed.metadata.pop("_raw_images", None)
        if raw_images:
            saved = save_doc_images(doc_id, paths.images_dir / doc_id, raw_images)
            parsed.images.extend(saved)

        doc = Document(
            id=doc_id,
            source_path=str(src),
            kind=kind,
            title=parsed.title or src.stem,
            metadata=dict(parsed.metadata),
            markdown_path=markdown_path,
            image_dir=image_dir,
            sections=[],
            images=list(parsed.images),
            n_chunks=len(chunks),
            n_tokens=sum(len(c.text) // 4 for c in chunks),
        )
        write_document(paths, doc, parsed.markdown, chunks)
        docs.append(doc)
        all_chunks_list.extend(chunks)
        docs_chunks_pairs.append((doc_id, chunks))
        if isinstance(parsed.metadata.get("keywords"), list):
            declared[doc_id] = parsed.metadata["keywords"]

    # embed everything
    if all_chunks_list:
        matrix = embed_texts([c.text for c in all_chunks_list])
        store = VectorStore(ids=[c.id for c in all_chunks_list], matrix=matrix)
    else:
        store = VectorStore(ids=[], matrix=embed_texts([]))
    write_vector_store(paths, store)

    graph = build_corpus_graph(docs, all_chunks_list, store)
    write_graph(paths, graph)

    vocab = extract_topics(docs_chunks_pairs, declared_per_doc=declared)
    write_topics(paths.topics_path, vocab)

    return paths


def _iter_sources(root: Path):
    exts = {".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".html", ".htm"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def _doc_id_for(path: Path) -> str:
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}_{h}"
