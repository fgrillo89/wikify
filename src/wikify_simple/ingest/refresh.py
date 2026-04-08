"""Single entry point: ``ingest_corpus(input_dir, output_dir)``.

Walks an input directory, parses every supported file, chunks it, embeds
chunks + image captions, materialises the corpus graph, extracts the
topic vocabulary, and writes everything under ``output_dir``. Idempotent
modulo file content (file hash → doc id).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..models import Chunk, DocSection, Document
from ..paths import CorpusPaths
from ..store.corpus import (
    write_document,
    write_graph,
    write_vector_store,
)
from ..store.images_index import build_images_index
from ..store.vectors import VectorStore
from ..store.vectors_meta import VectorsMeta
from ..store.vectors_meta import write_meta as write_vectors_meta
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
        # Image folder uses a clean human-readable slug (word-bounded,
        # no hash, ≤80 chars) so on-disk paths stay well under Windows
        # MAX_PATH and are easy to inspect. doc_id (with hash) remains
        # the corpus index key; image_dir is the bucket on disk.
        image_slug = _image_slug(src)
        image_dir_path = paths.images_dir / image_slug
        # Store as absolute path so read_doc_images works regardless of
        # the caller's cwd. Corpora are not relocatable today; if that
        # changes, swap to a path relative to corpus root.
        image_dir = str(image_dir_path)

        raw_images = parsed.metadata.pop("_raw_images", None)
        if raw_images:
            saved = save_doc_images(doc_id, image_dir_path, raw_images)
            parsed.images.extend(saved)

        sections = _sections_from_chunks(chunks)

        doc = Document(
            id=doc_id,
            source_path=str(src),
            kind=kind,
            title=parsed.title or src.stem,
            metadata=dict(parsed.metadata),
            markdown_path=markdown_path,
            image_dir=image_dir,
            sections=sections,
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

    # Persist embedder backend metadata so eval/query can reconstruct the
    # exact same embedder later. Dim is the matrix's actual width.
    from ..infra.embedding import current_backend

    backend = current_backend()
    meta = VectorsMeta(
        backend=str(backend["backend"]),
        dim=int(store.matrix.shape[1]) if store.matrix.size else int(backend["dim"] or 0),
        model=backend["model"],  # type: ignore[arg-type]
    )
    write_vectors_meta(paths.vectors_path, meta)

    graph = build_corpus_graph(docs, all_chunks_list, store)
    write_graph(paths, graph)

    vocab = extract_topics(docs_chunks_pairs, declared_per_doc=declared)
    write_topics(paths.topics_path, vocab)

    # Build the per-corpus image index from the sidecars just written.
    # Source-of-truth remains the sidecars; this is a single-file
    # projection the wiki/distill side reads to look up figures by
    # caption label or doc.
    build_images_index(paths, doc_ids=[d.id for d in docs])

    return paths


def _iter_sources(root: Path):
    exts = {".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".html", ".htm"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def _doc_id_for(path: Path) -> str:
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}_{h}"


def _image_slug(path: Path) -> str:
    """Filesystem-safe folder name from a paper filename.

    Drops bracket/punctuation noise, collapses whitespace to underscores,
    and truncates at a word boundary so the folder never ends mid-word
    (``..._Computing_Applicat``). Capped at 80 chars to stay well under
    Windows MAX_PATH. The doc_id (with content hash) remains the corpus
    index key; this is the human-friendly bucket on disk.
    """
    stem = path.stem
    slug = re.sub(r"[^\w\s-]", "", stem)
    slug = re.sub(r"\s+", "_", slug).strip("_")
    if len(slug) <= 80:
        return slug or hashlib.sha1(stem.encode("utf-8")).hexdigest()[:12]
    cut = slug[:80].rsplit("_", 1)[0]
    return cut or hashlib.sha1(stem.encode("utf-8")).hexdigest()[:12]


def _sections_from_chunks(chunks: list[Chunk]) -> list[DocSection]:
    """Group chunks by their section_path into DocSection records.

    The chunker already carries section_path on every Chunk; this just
    folds them into the per-section index the rest of the pipeline reads
    from ``Document.sections``. Order is preserved (first appearance
    wins). Image-caption chunks (section_path starts with "__image__")
    are excluded.
    """
    out: list[DocSection] = []
    by_key: dict[tuple[str, ...], DocSection] = {}
    for c in chunks:
        path = list(c.section_path or [])
        if path and path[0] == "__image__":
            continue
        if not path:
            path = ["body"]
        key = tuple(path)
        sec = by_key.get(key)
        if sec is None:
            sec = DocSection(path=path, chunk_ids=[])
            by_key[key] = sec
            out.append(sec)
        sec.chunk_ids.append(c.id)
    return out
