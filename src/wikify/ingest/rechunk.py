"""Re-chunk an existing corpus from saved markdown -- no parser, no models.

When the chunker changes (e.g. hygiene improvements, HybridChunker
swap, parameter tweaks), there is no need to re-run Marker / Docling
on the source PDFs. The pipeline already persists each doc's
canonical markdown to ``markdown/<doc_id>.md``, plus image sidecars
under ``images/<slug>/``. This module walks that disk state and
rewrites the chunk-derived artefacts in place: per-doc
``chunks/<id>.jsonl``, the chunk subset of ``docs/<id>.json``
(``n_chunks``, ``sections``, ``equations``, ``citations``,
``figure_refs``), and -- via ``refresh_corpus`` -- every downstream
SQLite-store derivative (chunks table, embeddings, graph edges,
chunk_citations, etc.).

Cost on the typical corpus is dominated by chunking + embedding:
chunking is ~3-4 s/doc warm, embedding is incremental at
~1-2 s/doc, so a 200-doc corpus rechunks in roughly 15 minutes
versus the multi-hour cost of a full Marker re-ingest.

Wave A of the refresh DAG (citation enrichment via OpenAlex) is
skipped when ``resolve_bibliography_doi=False`` because nothing about
chunking changes the source bibliographies; the existing
``bib_entries`` table is reused as-is. Pass
``resolve_bibliography_doi=True`` if you want the resolver to run
again anyway.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from ..api import Corpus
from ..models import Document
from .equations import extract_equations
from .figure_refs import extract_figure_refs
from .hybrid_chunker import chunk_with_hybrid
from .images import (
    caption_chunks_for,
    link_chunks_to_images,
    load_sidecars,
    rewrite_sidecar_near_chunks,
)


def _default_workers() -> int:
    """Same heuristic the parse pipeline uses: 60% of cores, min 2."""
    cpu = os.cpu_count() or 2
    return max(2, int(cpu * 0.6))


def _load_doc(paths: Corpus, doc_id: str) -> Document | None:
    """Load the persisted Document record for *doc_id*."""
    import json

    from wikify.corpus.chunks import _doc_from_dict

    f = paths.docs_dir / f"{doc_id}.json"
    if not f.exists():
        return None
    return _doc_from_dict(json.loads(f.read_text(encoding="utf-8")))


def _markdown_for(paths: Corpus, doc_id: str) -> str | None:
    f = paths.markdown_dir / f"{doc_id}.md"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8")


def rechunk_doc(paths: Corpus, doc_id: str) -> int:
    """Re-chunk one doc in place. Returns the new chunk count.

    Reads markdown + image sidecars from disk, runs the universal
    HybridChunker, re-extracts equations / citations / figure_refs
    from the same markdown, persists the new chunks JSONL, and
    rewrites the Document JSON's chunk-derived fields. Image
    near-chunk pointers are recomputed too because new chunk ids
    invalidate the old links.
    """
    from wikify.corpus.chunks import write_document

    from .citations import extract_citations
    from .pipeline import bind_equations_to_chunks, sections_from_chunks

    md = _markdown_for(paths, doc_id)
    if md is None:
        raise FileNotFoundError(
            f"markdown for {doc_id!r} missing under {paths.markdown_dir}"
        )
    doc = _load_doc(paths, doc_id)
    if doc is None:
        raise FileNotFoundError(
            f"doc record for {doc_id!r} missing under {paths.docs_dir}"
        )

    chunks = chunk_with_hybrid(doc_id, md)

    # Caption chunks come from already-persisted image sidecars.
    img_dir = Path(doc.image_dir)
    if not img_dir.is_absolute():
        img_dir = paths.root / img_dir
    images = load_sidecars(img_dir) if img_dir.exists() else []
    chunks += caption_chunks_for(doc_id, images, ord_offset=len(chunks))

    equations = extract_equations(md)
    figure_refs = extract_figure_refs(md)
    bind_equations_to_chunks(chunks, equations, use_text_match=True)

    citations = extract_citations(md, doc_id)
    near_map = link_chunks_to_images(chunks, images)
    if img_dir.exists():
        rewrite_sidecar_near_chunks(img_dir, near_map)

    new_doc = Document(
        id=doc.id,
        source_path=doc.source_path,
        kind=doc.kind,
        title=doc.title,
        metadata=dict(doc.metadata),
        markdown_path=doc.markdown_path,
        image_dir=doc.image_dir,
        sections=sections_from_chunks(chunks),
        images=list(images),
        abstract=doc.abstract,
        tldr=doc.tldr,
        n_chunks=len(chunks),
        n_tokens=sum(len(c.text) // 4 for c in chunks),
        citations=citations,
        equations=list(equations),
        figure_refs=list(figure_refs),
        similar_to=list(doc.similar_to),
        cites=list(doc.cites),
    )
    write_document(paths, new_doc, md, chunks)
    return len(chunks)


def _rechunk_doc_worker(args: tuple[str, str]) -> tuple[str, int, str | None]:
    """Process-pool wrapper around ``rechunk_doc``.

    Each worker reconstructs the ``Corpus`` from the root string
    because dataclasses with ``Path`` fields don't pickle reliably
    across process boundaries. Returns ``(doc_id, n_chunks, error)``;
    a non-None ``error`` lets the orchestrator log per-doc failures
    without aborting the batch.
    """
    corpus_root, doc_id = args
    try:
        corpus = Corpus(root=Path(corpus_root))
        n = rechunk_doc(corpus, doc_id)
    except Exception as exc:  # noqa: BLE001
        return doc_id, 0, f"{type(exc).__name__}: {exc}"
    return doc_id, n, None


def rechunk_corpus(
    paths: Corpus,
    *,
    only_docs: Iterable[str] | None = None,
    resolve_bibliography_doi: bool = False,
    cite_resolution: str = "off",
    max_workers: int | None = None,
) -> dict:
    """Re-chunk every doc on disk and refresh derived artefacts.

    Runs chunking in a ``ProcessPoolExecutor`` -- each worker
    independently loads the chunker (HybridChunker is module-level
    cached, so the load amortises after the first call per worker).
    Pass ``max_workers=1`` to force serial execution; the default
    follows the parse pipeline's 60%-of-cores heuristic.

    Pass ``only_docs`` to limit to a subset (useful when iterating on
    chunker tweaks against a few specific docs). The default refresh
    skips OpenAlex enrichment because chunking changes do not affect
    bibliographies; pass ``resolve_bibliography_doi=True`` to force
    the wave-A network calls regardless.
    """
    from wikify.corpus.chunks import list_documents

    from .pipeline import refresh_corpus

    docs = list_documents(paths)
    if only_docs is not None:
        wanted = set(only_docs)
        docs = [d for d in docs if d.id in wanted]
    if not docs:
        print("[rechunk] no docs to rechunk", file=sys.stderr)
        return {"docs": 0, "chunks": 0, "rechunk_seconds": 0.0}

    n_workers = max_workers if max_workers and max_workers > 0 else _default_workers()
    print(
        f"[rechunk] {len(docs)} docs / {n_workers} workers",
        file=sys.stderr,
    )

    t0 = time.monotonic()
    total_chunks = 0
    completed = 0
    work = [(str(paths.root), d.id) for d in docs]
    if n_workers <= 1:
        # Serial path keeps the in-process module cache hot; avoids
        # ProcessPoolExecutor startup cost on tiny corpora.
        for args in work:
            doc_id, n, err = _rechunk_doc_worker(args)
            completed += 1
            if err:
                print(f"  [skip] {doc_id}: {err}", file=sys.stderr)
                continue
            total_chunks += n
            if completed % 10 == 0 or completed == len(work):
                print(
                    f"  [rechunk] {completed}/{len(work)} done "
                    f"({total_chunks} chunks, {time.monotonic()-t0:.1f}s)",
                    file=sys.stderr,
                )
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_rechunk_doc_worker, args): args[1]
                       for args in work}
            for fut in as_completed(futures):
                doc_id, n, err = fut.result()
                completed += 1
                if err:
                    print(f"  [skip] {doc_id}: {err}", file=sys.stderr)
                    continue
                total_chunks += n
                if completed % 10 == 0 or completed == len(work):
                    print(
                        f"  [rechunk] {completed}/{len(work)} done "
                        f"({total_chunks} chunks, "
                        f"{time.monotonic()-t0:.1f}s)",
                        file=sys.stderr,
                    )

    rechunk_seconds = time.monotonic() - t0
    print(
        f"[rechunk] chunked {len(docs)} docs / {total_chunks} chunks "
        f"in {rechunk_seconds:.1f}s; refreshing derived artefacts ...",
        file=sys.stderr,
    )

    # Mark every doc as stale so embeddings + graph rows get rebuilt.
    stale = {d.id for d in docs}
    refresh_corpus(
        paths,
        stale_doc_ids=stale,
        resolve_bibliography_doi=resolve_bibliography_doi,
        cite_resolution=cite_resolution,
    )
    return {
        "docs": len(docs),
        "chunks": total_chunks,
        "rechunk_seconds": round(rechunk_seconds, 1),
        "total_seconds": round(time.monotonic() - t0, 1),
    }
