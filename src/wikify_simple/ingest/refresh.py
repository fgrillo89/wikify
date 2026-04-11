"""Single entry point: ``ingest_corpus(input_dir, output_dir)``.

Walks an input directory, parses every supported file, chunks it, embeds
chunks + image captions, materialises the corpus graph, extracts the
topic vocabulary, and writes everything under ``output_dir``. Idempotent
modulo file content (file hash → doc id).
"""

import hashlib
import re
from pathlib import Path

from ..infra.embedding import embed_texts
from ..models import Chunk, DocSection, Document
from ..paths import CorpusPaths
from ..store.corpus import (
    write_document,
    write_graph,
    write_vector_store,
)
from ..store.doc_markdown import write_doc_markdown
from ..store.images_index import build_images_index
from ..store.vectors import VectorStore
from ..store.vectors_meta import VectorsMeta
from ..store.vectors_meta import write_meta as write_vectors_meta
from .bibtex import write_corpus_bibtex
from .chunker import chunk_document
from .citations import extract_citations
from .corpus_graph import build_corpus_graph
from .coupling import compute_coupling
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
    raw_markdown_by_id: dict[str, str] = {}

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
            citations=extract_citations(parsed.markdown, doc_id),
        )
        write_document(paths, doc, parsed.markdown, chunks)
        raw_markdown_by_id[doc_id] = parsed.markdown
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

    # Corpus-wide BibTeX library (one entry per Document).
    write_corpus_bibtex(paths, docs)

    # --- doc-level edges: similar_to, cites, cites_same --------------
    _populate_doc_edges(docs, docs_chunks_pairs, store)

    # Persist updated Document JSON (now with edges) and overwrite the
    # per-doc markdown with the Obsidian-friendly enriched rendering.
    import json as _json

    from ..store.corpus import _doc_to_dict  # internal helper reuse

    for doc in docs:
        (paths.docs_dir / f"{doc.id}.json").write_text(
            _json.dumps(_doc_to_dict(doc)), encoding="utf-8"
        )
        body = raw_markdown_by_id.get(doc.id, "")
        write_doc_markdown(paths, doc, body)

    return paths


def _populate_doc_edges(
    docs: list[Document],
    docs_chunks_pairs: list[tuple[str, list[Chunk]]],
    store: VectorStore,
) -> None:
    """Fill in ``similar_to`` / ``cites`` / ``cites_same`` for every doc."""
    import numpy as np

    # 1. similar_to: mean-pooled chunk cosine, top-K above 0.7.
    chunk_by_id = {cid: i for i, cid in enumerate(store.ids)}
    matrix = store.matrix
    doc_vecs: dict[str, np.ndarray] = {}
    for doc_id, chunks in docs_chunks_pairs:
        rows = [chunk_by_id[c.id] for c in chunks if c.id in chunk_by_id]
        if not rows or matrix.size == 0:
            continue
        mean = matrix[rows].mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm > 0:
            mean = mean / norm
        doc_vecs[doc_id] = mean

    doc_ids = list(doc_vecs.keys())
    if doc_ids:
        stacked = np.stack([doc_vecs[d] for d in doc_ids], axis=0)
        sims = stacked @ stacked.T
        for i, d_id in enumerate(doc_ids):
            ranked = [
                (float(sims[i, j]), doc_ids[j])
                for j in range(len(doc_ids))
                if j != i and float(sims[i, j]) >= 0.7
            ]
            ranked.sort(key=lambda x: (-x[0], x[1]))
            top = [other for _, other in ranked[:5]]
            for doc in docs:
                if doc.id == d_id:
                    doc.similar_to = top
                    break

    # 2. cites: resolve each citation to a corpus doc by fuzzy
    #    (title/authors/year) match.
    title_to_id: dict[str, str] = {}
    for doc in docs:
        key = _normalize_title(doc.title)
        if key:
            title_to_id[key] = doc.id

    for doc in docs:
        resolved: list[str] = []
        seen: set[str] = set()
        for cit in doc.citations or []:
            title = cit.get("title") or ""
            key = _normalize_title(str(title))
            if key and key in title_to_id:
                target = title_to_id[key]
                if target != doc.id and target not in seen:
                    seen.add(target)
                    resolved.append(target)
        doc.cites = resolved

    # 3. cites_same: bibliographic coupling on shared references.
    coupling = compute_coupling(docs, min_strength=3, top_k=5)
    for doc in docs:
        doc.cites_same = coupling.get(doc.id, [])


def _normalize_title(title: str) -> str:
    """Lowercase alnum-only title fingerprint for fuzzy matching."""
    s = (title or "").lower()
    out: list[str] = []
    for ch in s:
        if ch.isalnum() or ch == " ":
            out.append(ch)
        else:
            out.append(" ")
    collapsed = " ".join("".join(out).split())
    return collapsed


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
