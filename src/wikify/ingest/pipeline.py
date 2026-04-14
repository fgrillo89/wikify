"""Ingest pipeline: ``ingest_corpus(input_dir, output_dir)``.

Walks an input directory, parses every supported file, chunks it, embeds
chunks + image captions, materialises the corpus graph, extracts the
topic vocabulary, and writes everything under ``output_dir``.

The pipeline separates *source processing* (per-file: parse, chunk, enrich,
persist -- incremental) from *derived rebuild* (corpus-wide: embed, edges,
graph, index, pagerank, topics, images, bibtex -- must see the full active
corpus).
"""

import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from ..embedding import embed_texts
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
from .bibtex import write_corpus_bibliography
from .chunker import chunk_document
from .citations import extract_citations
from .config import DOC_SIM_COS
from .corpus_graph import build_corpus_graph
from .coupling import compute_coupling
from .equations import extract_equations
from .explorer_index import build_explorer_index, save_explorer_index
from .figure_refs import extract_figure_refs
from .images import (
    caption_chunks_for,
    link_chunks_to_images,
    rewrite_sidecar_near_chunks,
    save_doc_images,
)
from .parsers.registry import parse_file, validate_backend
from .topics import extract_topics, write_topics

# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

@contextmanager
def _timed(timings: dict[str, float], label: str):
    t = time.monotonic()
    yield
    timings[label] = time.monotonic() - t


# ---------------------------------------------------------------------------
# Worker bundle
# ---------------------------------------------------------------------------

@dataclass
class FileReceipt:
    """Lightweight result from a parse+persist worker.

    Only carries identifiers and stats -- the full document, markdown,
    and chunks are already persisted to disk.
    """

    src_path: str
    doc_id: str
    n_chunks: int
    declared_keywords: list[str]
    parse_seconds: float


# ---------------------------------------------------------------------------
# Source processing helpers (per-file, parallelisable)
# ---------------------------------------------------------------------------

_SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".html", ".htm"}


def iter_sources(root: Path):
    """Yield every supported file under *root*."""
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTS:
            yield p


def doc_id_for(path: Path) -> str:
    """Stable doc id from source content: ``{stem}_{sha1[:12]}``."""
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}_{h}"


def content_hash(path: Path) -> str:
    """12-char sha1 prefix of file bytes -- same hash used in doc_id."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def image_slug(doc_id: str) -> str:
    """Filesystem-safe image folder name derived from doc_id.

    Uses doc_id (which includes the content hash) so same-stem sources
    in different directories get distinct image folders.  Truncated at
    80 chars on a word boundary for Windows MAX_PATH.
    """
    slug = re.sub(r"[^\w\s-]", "", doc_id)
    slug = re.sub(r"\s+", "_", slug).strip("_")
    if len(slug) <= 80:
        return slug or doc_id[:12]
    cut = slug[:80].rsplit("_", 1)[0]
    return cut or doc_id[:12]


def _default_workers() -> int:
    cpu = os.cpu_count() or 2
    return max(2, int(cpu * 0.6))


def _parse_and_persist_worker(
    src_str: str,
    corpus_root_str: str,
    parser_backend: str = "default",
) -> FileReceipt:
    """Parse, chunk, enrich, and persist one source file. Returns a lightweight receipt.

    Runs in a worker process. Reconstructs CorpusPaths from the root string
    since dataclasses with Path fields don't pickle reliably across processes.
    """
    src = Path(src_str)
    paths = CorpusPaths(root=Path(corpus_root_str))
    t_worker = time.monotonic()

    kind, parsed = parse_file(src, parser_backend=parser_backend)
    did = doc_id_for(src)

    # Images
    img_slug = image_slug(did)
    image_dir_path = paths.images_dir / img_slug
    if parsed.raw_images:
        saved = save_doc_images(did, image_dir_path, parsed.raw_images)
        parsed.images.extend(saved)

    # Chunks
    docling_chunks = parsed.metadata.pop("_docling_chunks", None)
    if docling_chunks:
        chunks = _chunks_from_docling(did, docling_chunks)
    else:
        chunks = chunk_document(did, parsed.markdown, parsed.sections)
    chunks += caption_chunks_for(did, parsed.images, ord_offset=len(chunks))

    # Equations + figure refs
    equations = extract_equations(parsed.markdown)
    figure_refs = extract_figure_refs(parsed.markdown)
    bind_equations_to_chunks(chunks, equations)

    # Citations + image linking + sections
    citations = extract_citations(parsed.markdown, did)
    near_map = link_chunks_to_images(chunks, parsed.images)
    rewrite_sidecar_near_chunks(image_dir_path, near_map)
    sections = sections_from_chunks(chunks)

    # Build Document
    doc = Document(
        id=did,
        source_path=str(src),
        kind=kind,
        title=parsed.title or src.stem,
        metadata=dict(parsed.metadata),
        markdown_path=str(paths.markdown_dir / f"{did}.md"),
        image_dir=str(image_dir_path),
        sections=sections,
        images=list(parsed.images),
        n_chunks=len(chunks),
        n_tokens=sum(len(c.text) // 4 for c in chunks),
        citations=citations,
        equations=list(equations),
        figure_refs=list(figure_refs),
    )

    # Persist atomically
    write_document(paths, doc, parsed.markdown, chunks)

    # Declared keywords for topic extraction
    kw = parsed.metadata.get("keywords")
    declared = list(kw) if isinstance(kw, list) else []

    elapsed = time.monotonic() - t_worker
    return FileReceipt(
        src_path=str(src),
        doc_id=did,
        n_chunks=len(chunks),
        declared_keywords=declared,
        parse_seconds=elapsed,
    )


def _chunks_from_docling(doc_id: str, docling_chunks: list[dict]) -> list[Chunk]:
    """Build Chunk objects from Docling's HybridChunker output."""
    import hashlib

    from .config import MIN_CHUNK_ALNUM
    from .section_classifier import classify_section_path

    chunks: list[Chunk] = []
    offset = 0
    for ord_, dc in enumerate(docling_chunks):
        text = dc["text"].strip()
        if not text:
            continue
        alnum = sum(1 for c in text if c.isalnum())
        if alnum < MIN_CHUNK_ALNUM:
            continue
        heading_path = dc.get("heading_path", ["body"])
        section_type = classify_section_path(heading_path).value
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        cid = f"{doc_id}__c{ord_:04d}__{h}"
        end = offset + len(text)
        chunks.append(
            Chunk(
                id=cid,
                doc_id=doc_id,
                ord=ord_,
                text=text,
                char_span=(offset, end),
                section_path=list(heading_path),
                section_type=section_type,
            )
        )
        offset = end
    return chunks


def bind_equations_to_chunks(chunks: list[Chunk], equations: list[dict]) -> None:
    """Attach equation ids to the chunks whose char_span contains them."""
    if not equations:
        return
    body_chunks = [c for c in chunks if not (c.section_path and c.section_path[0] == "__image__")]
    body_chunks.sort(key=lambda c: c.char_span[0])
    if not body_chunks:
        return
    for eq in equations:
        offset = int(eq.get("char_offset") or 0)
        for c in body_chunks:
            start, end = c.char_span
            if start <= offset < end:
                c.equation_ids.append(eq["id"])
                break


def sections_from_chunks(chunks: list[Chunk]) -> list[DocSection]:
    """Group chunks by their section_path into DocSection records."""
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


# ---------------------------------------------------------------------------
# Derived-artifact health check
# ---------------------------------------------------------------------------

def _derived_artifacts_missing(paths: CorpusPaths) -> bool:
    """True if the corpus has docs but is missing key derived artifacts.

    Catches the case where ingest completed but refresh crashed or was
    skipped -- re-running ``ingest`` should detect this and run refresh.
    """
    has_docs = paths.docs_dir.exists() and any(paths.docs_dir.iterdir())
    if not has_docs:
        return False
    # Check the three cheapest-to-verify derived artifacts.
    return (
        not paths.vectors_path.exists()
        or not (paths.root / "graph.json").exists()
        or not (paths.root / "topics.json").exists()
    )


# ---------------------------------------------------------------------------
# Crash recovery: detect already-persisted docs from a prior interrupted run
# ---------------------------------------------------------------------------

def _recover_completed(
    sources: list[Path],
    paths: CorpusPaths,
) -> tuple[list[Path], list[FileReceipt]]:
    """Split sources into (still_to_parse, already_done).

    If a prior ingest crashed after persisting some files but before
    saving the manifest, the doc JSON + chunks JSONL will be on disk
    without a manifest entry. We detect those and build synthetic
    receipts so they aren't re-parsed.
    """
    still: list[Path] = []
    recovered: list[FileReceipt] = []

    for src in sources:
        did = doc_id_for(src)
        doc_json = paths.docs_dir / f"{did}.json"
        chunks_jsonl = paths.chunks_dir / f"{did}.jsonl"
        md_file = paths.markdown_dir / f"{did}.md"

        if doc_json.exists() and chunks_jsonl.exists() and md_file.exists():
            # All three artifacts present -- build a synthetic receipt.
            chunk_ids = _read_chunk_ids(paths, did)
            # Read declared keywords from the persisted doc JSON.
            kw: list[str] = []
            try:
                doc_data = json.loads(doc_json.read_text(encoding="utf-8"))
                meta_kw = (doc_data.get("metadata") or {}).get("keywords")
                if isinstance(meta_kw, list):
                    kw = meta_kw
            except Exception:  # noqa: BLE001
                pass
            recovered.append(FileReceipt(
                src_path=str(src),
                doc_id=did,
                n_chunks=len(chunk_ids),
                declared_keywords=kw,
                parse_seconds=0.0,
            ))
        else:
            still.append(src)

    if recovered:
        print(
            f"[ingest] recovered {len(recovered)} docs from prior "
            f"interrupted run (skipping re-parse)",
            file=sys.stderr,
        )

    return still, recovered


# ---------------------------------------------------------------------------
# Stage: streaming parse + persist (new pipeline)
# ---------------------------------------------------------------------------

def _stream_parse_and_persist(
    sources: list[Path],
    paths: CorpusPaths,
    max_workers: int | None,
    parser_backend: str = "default",
) -> list[FileReceipt]:
    """Parse, persist each source in parallel. Returns sorted receipts."""
    from tqdm import tqdm

    workers = max_workers if max_workers is not None else _default_workers()
    receipts: list[FileReceipt] = []
    total = len(sources)
    corpus_root_str = str(paths.root)
    failed = 0

    # Docling uses a GPU model -- don't duplicate across processes.
    if parser_backend == "docling":
        workers = 1

    bar = tqdm(
        total=total,
        desc=f"[ingest] parse+persist ({workers}w)",
        unit="file",
        file=sys.stderr,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    if workers > 1 and total > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _parse_and_persist_worker, str(src), corpus_root_str,
                    parser_backend,
                ): src
                for src in sources
            }
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    receipt = fut.result()
                    receipts.append(receipt)
                    bar.set_postfix_str(
                        f"{receipt.parse_seconds:.1f}s {src.name[:40]}",
                        refresh=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    bar.set_postfix_str(
                        f"FAIL {src.name[:40]}",
                        refresh=False,
                    )
                    tqdm.write(
                        f"[ingest] FAIL {src.name}: {exc}",
                        file=sys.stderr,
                    )
                bar.update(1)
    else:
        for src in sources:
            try:
                receipt = _parse_and_persist_worker(
                    str(src), corpus_root_str, parser_backend,
                )
                receipts.append(receipt)
                bar.set_postfix_str(
                    f"{receipt.parse_seconds:.1f}s {src.name[:40]}",
                    refresh=False,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                bar.set_postfix_str(
                    f"FAIL {src.name[:40]}",
                    refresh=False,
                )
                tqdm.write(
                    f"[ingest] FAIL {src.name}: {exc}",
                    file=sys.stderr,
                )
            bar.update(1)

    bar.close()

    if failed:
        print(f"[ingest] {failed}/{total} files failed", file=sys.stderr)

    receipts.sort(key=lambda r: r.src_path)

    # Slowest-paper report
    slow = sorted(receipts, key=lambda r: -r.parse_seconds)[:5]
    if slow and slow[0].parse_seconds > 5.0:
        print("[ingest] slowest papers (parser CPU time):", file=sys.stderr)
        for r in slow:
            name = Path(r.src_path).name[:60]
            print(f"  {r.parse_seconds:6.2f}s  {name}", file=sys.stderr)

    return receipts



# ---------------------------------------------------------------------------
# Stage: embed
# ---------------------------------------------------------------------------

def _embed_chunks_incremental(
    all_chunks: list[Chunk],
    paths: CorpusPaths,
    stale_doc_ids: set[str],
) -> VectorStore:
    """Embed chunks, reusing existing vectors for unchanged chunks.

    Loads the existing vector store, keeps rows whose chunk ids are still
    active and not from stale docs, embeds only the new/changed chunks,
    and merges into one store. Validates that the result covers exactly
    the active chunk ids.

    If the embedder backend changed since the last run, all existing
    vectors are discarded and everything is re-embedded to prevent
    mixing vectors from incompatible embedding spaces.
    """
    import numpy as np

    from ..embedding import current_backend
    from ..store.vectors import load_vectors
    from ..store.vectors_meta import read_meta

    target_ids = [c.id for c in all_chunks]
    target_set = set(target_ids)

    # Check embedder fingerprint: skip reuse if backend/model/dim changed.
    backend = current_backend()
    embedder_changed = False
    if paths.vectors_path.exists():
        old_meta = read_meta(paths.vectors_path)
        if old_meta is not None:
            cur_fp = _embedder_fingerprint(backend)
            old_fp = f"{old_meta.backend}:{old_meta.model}:{old_meta.dim}"
            if cur_fp != old_fp:
                embedder_changed = True
                print(
                    f"[ingest] embedder changed ({old_fp} -> {cur_fp}), "
                    f"re-embedding all",
                    file=sys.stderr,
                )

    # Try to load existing vectors for reuse
    reusable: dict[str, np.ndarray] = {}
    if (
        paths.vectors_path.exists()
        and not embedder_changed
        and not stale_doc_ids == target_set
    ):
        try:
            old_store = load_vectors(paths.vectors_path)
            for i, cid in enumerate(old_store.ids):
                if cid in target_set:
                    reusable[cid] = old_store.matrix[i]
        except Exception:  # noqa: BLE001
            pass  # corrupt or incompatible -- re-embed everything

    # Determine which chunks need fresh embedding
    to_embed = [c for c in all_chunks if c.id not in reusable]

    if to_embed:
        new_matrix = embed_texts([c.text for c in to_embed])
        for i, c in enumerate(to_embed):
            reusable[c.id] = new_matrix[i]

    n_reused = len(target_ids) - len(to_embed)
    n_embedded = len(to_embed)
    if n_reused > 0:
        print(
            f"[ingest] vectors: {n_reused} reused, {n_embedded} embedded",
            file=sys.stderr,
        )

    # Assemble final store in target_ids order
    if target_ids:
        rows = [reusable[cid] for cid in target_ids]
        import numpy as _np

        matrix = _np.stack(rows, axis=0)
    else:
        matrix = embed_texts([])
    store = VectorStore(ids=target_ids, matrix=matrix)

    # Validate: vector ids == active chunk ids
    assert set(store.ids) == target_set, (
        f"vector/chunk mismatch: {len(store.ids)} vectors, "
        f"{len(target_set)} chunks"
    )

    write_vector_store(paths, store)

    meta = VectorsMeta(
        backend=str(backend["backend"]),
        dim=int(store.matrix.shape[1]) if store.matrix.size else int(backend.get("dim") or 0),
        model=backend.get("model"),  # type: ignore[arg-type]
    )
    write_vectors_meta(paths.vectors_path, meta)
    return store


# ---------------------------------------------------------------------------
# Stage: doc-level edges
# ---------------------------------------------------------------------------

def populate_doc_edges(
    docs: list[Document],
    docs_chunks_pairs: list[tuple[str, list[Chunk]]],
    store: VectorStore,
) -> None:
    """Fill in ``similar_to`` / ``cites`` / ``cites_same`` for every doc."""
    import numpy as np

    # 1. similar_to: mean-pooled chunk cosine, top-K above threshold.
    chunk_by_id = {cid: i for i, cid in enumerate(store.ids)}
    matrix = store.matrix
    doc_vecs: dict[str, np.ndarray] = {}
    for did, chunks in docs_chunks_pairs:
        rows = [chunk_by_id[c.id] for c in chunks if c.id in chunk_by_id]
        if not rows or matrix.size == 0:
            continue
        mean = matrix[rows].mean(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm > 0:
            mean = mean / norm
        doc_vecs[did] = mean

    doc_ids = list(doc_vecs.keys())
    if doc_ids:
        stacked = np.stack([doc_vecs[d] for d in doc_ids], axis=0)
        sims = stacked @ stacked.T
        for i, d_id in enumerate(doc_ids):
            ranked = [
                (float(sims[i, j]), doc_ids[j])
                for j in range(len(doc_ids))
                if j != i and float(sims[i, j]) >= DOC_SIM_COS
            ]
            ranked.sort(key=lambda x: (-x[0], x[1]))
            top = [other for _, other in ranked[:5]]
            for doc in docs:
                if doc.id == d_id:
                    doc.similar_to = top
                    break

    # 2. cites: year-bucketed fuzzy citation matching.
    _resolve_citations(docs)

    # 3. cites_same: bibliographic coupling on shared references.
    coupling = compute_coupling(docs, min_strength=3, top_k=5)
    for doc in docs:
        doc.cites_same = coupling.get(doc.id, [])


def _resolve_citations(docs: list[Document]) -> None:
    """Resolve each doc's raw citations to corpus doc ids."""
    doc_index_by_year: dict[int, list[tuple[Document, set[str], list[str]]]] = {}
    for doc in docs:
        year = _safe_int(doc.metadata.get("year"))
        if year is None or not doc.title:
            continue
        title_words = _title_word_set(doc.title)
        last_names: list[str] = []
        for a in doc.metadata.get("authors") or []:
            toks = str(a).strip().split()
            if toks:
                last_names.append(_normalize_title(toks[-1]))
        doc_index_by_year.setdefault(year, []).append((doc, title_words, last_names))

    for doc in docs:
        resolved: list[str] = []
        seen: set[str] = set()
        for cit in doc.citations or []:
            if hasattr(cit, "year"):
                cit_year = _safe_int(cit.year)
                raw = str(cit.raw_text or cit.title or "")
                cit_last_names = cit.author_last_names or []
            else:
                cit_year = _safe_int(cit.get("year"))
                raw = str(cit.get("raw_text") or cit.get("title") or "")
                cit_last_names = cit.get("author_last_names") or []
            if cit_year is None or not raw:
                continue
            candidates = doc_index_by_year.get(cit_year, [])
            if not candidates:
                continue
            raw_norm = _normalize_title(raw)
            raw_words = set(raw_norm.split())
            best: Document | None = None
            best_score = 0
            for cand_doc, cand_title_words, cand_last_names in candidates:
                if cand_doc.id == doc.id:
                    continue
                score = 0
                for ln in cand_last_names:
                    if len(ln) >= 3 and ln in raw_norm:
                        score += 3
                        break
                # Boost for direct last-name match
                for cln in cit_last_names:
                    cln_norm = _normalize_title(cln)
                    if cln_norm in {ln for ln in cand_last_names}:
                        score += 2
                        break
                score += len(cand_title_words & raw_words)
                if score >= 3 and score > best_score:
                    best_score = score
                    best = cand_doc
            if best is not None and best.id not in seen:
                seen.add(best.id)
                resolved.append(best.id)
        doc.cites = resolved


# ---------------------------------------------------------------------------
# Stage: pagerank
# ---------------------------------------------------------------------------

def write_pagerank(paths: CorpusPaths, docs: list[Document], graph) -> None:
    """Compute PageRank on the doc graph and persist to pagerank.json."""
    doc_ids = [d.id for d in docs]
    g = nx.Graph()
    g.add_nodes_from(doc_ids)
    if graph is not None:
        node_set = set(doc_ids)
        for etype in ("cites", "doc_similar", "cites_same"):
            for src, dst in graph.edges.get(etype, []):
                if src not in node_set or dst not in node_set or src == dst:
                    continue
                if g.has_edge(src, dst):
                    g[src][dst]["weight"] += 1.0
                else:
                    g.add_edge(src, dst, weight=1.0)
    if g.number_of_nodes() == 0:
        pagerank: dict[str, float] = {}
    else:
        pagerank = dict(nx.pagerank(g, weight="weight"))
    from ..store.corpus import atomic_write_text

    atomic_write_text(paths.pagerank_path, json.dumps(pagerank))


# ---------------------------------------------------------------------------
# Embedder fingerprint
# ---------------------------------------------------------------------------

def _embedder_fingerprint(backend: dict) -> str:
    """Single fingerprint string for backend + model + dim."""
    return f"{backend['backend']}:{backend.get('model', '')}:{backend.get('dim', '')}"


# ---------------------------------------------------------------------------
# Stage: doc resave (with populated edges)
# ---------------------------------------------------------------------------

def _resave_docs(
    paths: CorpusPaths,
    docs: list[Document],
) -> None:
    from ..store.corpus import _doc_to_dict, atomic_write_text

    for doc in docs:
        atomic_write_text(
            paths.docs_dir / f"{doc.id}.json",
            json.dumps(_doc_to_dict(doc)),
        )
        md_path = paths.markdown_dir / f"{doc.id}.md"
        if md_path.exists():
            body = _read_body_from_doc_markdown(md_path)
        else:
            body = ""
        write_doc_markdown(paths, doc, body)


def _read_body_from_doc_markdown(md_path: Path) -> str:
    """Read the body text from an enriched doc markdown file.

    The file has YAML frontmatter (``---`` ... ``---``) followed by the
    body, then an ``## Edges`` section. We strip frontmatter and edges
    to recover the original parsed body.
    """
    text = md_path.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    # Strip the ## Edges section appended by write_doc_markdown
    edges_idx = text.find("\n## Edges\n")
    if edges_idx != -1:
        text = text[:edges_idx]
    return text.strip()


# ---------------------------------------------------------------------------
# String helpers for citation matching
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    s = (title or "").lower()
    out: list[str] = []
    for ch in s:
        if ch.isalnum() or ch == " ":
            out.append(ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def _title_word_set(title: str, min_len: int = 4) -> set[str]:
    return {w for w in _normalize_title(title).split() if len(w) >= min_len}


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Timing report
# ---------------------------------------------------------------------------

def _print_timings(timings: dict[str, float], t0: float) -> None:
    total = time.monotonic() - t0
    print("[ingest] timing report:", file=sys.stderr)
    for stage, secs in timings.items():
        pct = 100.0 * secs / total if total > 0 else 0.0
        print(f"  {stage:30}  {secs:7.2f}s  ({pct:5.1f}%)", file=sys.stderr)
    print(f"  {'TOTAL':30}  {total:7.2f}s", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pipeline stages (called from ingest_corpus)
# ---------------------------------------------------------------------------


def _prepare_change_set(
    input_dir: Path,
    paths: CorpusPaths,
    mode: str,
    timings: dict[str, float],
) -> tuple:
    """Enumerate sources, diff against manifest, deduplicate.

    Returns ``(manifest, change_set, dedup_aliases)`` or ``None`` when
    nothing has changed.
    """
    from .manifest import (
        CorpusManifest,
        diff_sources,
        source_id_for,
    )

    with _timed(timings, "enumerate+dedupe"):
        manifest = CorpusManifest.load(paths.manifest_path)
        raw_sources = sorted(iter_sources(input_dir))
        change_set = diff_sources(
            raw_sources, manifest, input_root=input_dir, mode=mode,
        )

        # hash_to_doc: content_hash -> persisted doc_id.  Seeded from
        # active manifest records so cross-run duplicates are caught.
        hash_to_doc: dict[str, str] = {
            s.content_hash: s.doc_id
            for s in manifest.sources.values()
            if s.status == "active"
        }
        deduped: list[Path] = []
        dedup_aliases: list[tuple[str, str, str]] = []  # (sid, h, did)
        seen_this_run: set[str] = set()
        for src in change_set.to_parse:
            try:
                h = content_hash(src)
            except OSError:
                deduped.append(src)
                continue
            sid = change_set.path_to_sid.get(str(src))
            if sid is None:
                sid = source_id_for(src, input_dir)

            if h in seen_this_run:
                persisted_did = hash_to_doc.get(h, doc_id_for(src))
                dedup_aliases.append((sid, h, persisted_did))
                print(f"  [skip-intra] {src.name}", file=sys.stderr)
                continue

            if h in hash_to_doc:
                dedup_aliases.append((sid, h, hash_to_doc[h]))
                print(f"  [skip-cross] {src.name}", file=sys.stderr)
                continue

            seen_this_run.add(h)
            hash_to_doc[h] = doc_id_for(src)
            deduped.append(src)
        change_set.to_parse = deduped

    n_unchanged = len(change_set.unchanged)
    n_new = len(change_set.to_parse)
    n_delete = len(change_set.to_delete)
    n_replace = len(change_set.to_replace)
    print(
        f"[ingest] {n_unchanged} unchanged, {n_new} to parse "
        f"({n_replace} replacements), {n_delete} to delete",
        file=sys.stderr,
    )

    return manifest, change_set, dedup_aliases


def _identify_stale_docs(
    receipts: list[FileReceipt],
    dedup_aliases: list[tuple[str, str, str]],
    change_set,
    manifest,
    input_dir: Path,
) -> set[str]:
    """Determine which old doc_ids are stale after successful parses/aliases."""
    from .manifest import source_id_for

    parsed_sids: set[str] = set()
    for receipt in receipts:
        sid = change_set.path_to_sid.get(receipt.src_path)
        if sid is None:
            sid = source_id_for(Path(receipt.src_path), input_dir)
        parsed_sids.add(sid)

    aliased_sids = {sid for sid, _, _ in dedup_aliases}
    stale_doc_ids: set[str] = set()

    for sid, old_doc_id in change_set.to_replace.items():
        if sid in parsed_sids or sid in aliased_sids:
            stale_doc_ids.add(old_doc_id)
            print(f"  [replace] {sid}: old {old_doc_id}", file=sys.stderr)
        else:
            print(
                f"  [replace-skipped] {sid}: parse failed, "
                f"keeping {old_doc_id}",
                file=sys.stderr,
            )

    for sid in change_set.to_delete:
        rec = manifest.sources.get(sid)
        if rec and rec.status == "active":
            stale_doc_ids.add(rec.doc_id)
            manifest.sources[sid].status = "deleted"
            print(f"  [delete] {sid} ({rec.doc_id})", file=sys.stderr)

    return stale_doc_ids


def _read_chunk_ids(paths: CorpusPaths, doc_id: str) -> list[str]:
    """Read just the chunk ids from a persisted JSONL file (no text loaded)."""
    p = paths.chunks_dir / f"{doc_id}.jsonl"
    if not p.exists():
        return []
    ids: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.append(json.loads(line)["id"])
    return ids


def _update_manifest(
    manifest,
    receipts: list[FileReceipt],
    dedup_aliases: list[tuple[str, str, str]],
    change_set,
    paths: CorpusPaths,
    input_dir: Path,
) -> None:
    """Register parsed sources and dedup aliases in the manifest."""
    from .manifest import SourceRecord, source_id_for

    for receipt in receipts:
        sid = change_set.path_to_sid.get(receipt.src_path)
        if sid is None:
            sid = source_id_for(Path(receipt.src_path), input_dir)
        h = receipt.doc_id.rsplit("_", 1)[-1]
        manifest.sources[sid] = SourceRecord(
            source_id=sid,
            source_path=receipt.src_path,
            content_hash=h,
            doc_id=receipt.doc_id,
            status="active",
            chunk_ids=_read_chunk_ids(paths, receipt.doc_id),
            parsed_at=SourceRecord.now_iso(),
        )

    # Register dedup aliases only if the target doc_id actually exists.
    persisted_doc_ids = {r.doc_id for r in receipts}
    for alias_sid, alias_h, alias_did in dedup_aliases:
        target_on_disk = (paths.docs_dir / f"{alias_did}.json").exists()
        if alias_did not in persisted_doc_ids and not target_on_disk:
            print(
                f"  [alias-skipped] {alias_sid}: target {alias_did} "
                f"not on disk (canonical parse may have failed)",
                file=sys.stderr,
            )
            continue
        manifest.sources[alias_sid] = SourceRecord(
            source_id=alias_sid,
            source_path=(
                manifest.sources[alias_sid].source_path
                if alias_sid in manifest.sources
                else ""
            ),
            content_hash=alias_h,
            doc_id=alias_did,
            status="active",
            parsed_at=SourceRecord.now_iso(),
        )



# ---------------------------------------------------------------------------
# Public: refresh corpus-wide derived artifacts
# ---------------------------------------------------------------------------


def refresh_corpus(
    paths: CorpusPaths,
    *,
    stale_doc_ids: set[str] | None = None,
    resolve_bibliography_doi: bool = False,
) -> None:
    """Rebuild derived artifacts (embeddings, graph, topics, etc.).

    Loads the active corpus from disk, embeds chunks, then runs the
    refresh DAG defined below.  Each wave runs its steps in parallel;
    waves run sequentially so later steps can depend on earlier results.

    To add a new derived artifact: add a ``_refresh_*`` function and
    place its name in the appropriate wave (or add a new wave).
    """
    from concurrent.futures import ThreadPoolExecutor

    from ..store.corpus import all_chunks as load_all_chunks
    from ..store.corpus import list_documents

    timings: dict[str, float] = {}
    t0 = time.monotonic()

    if stale_doc_ids is None:
        stale_doc_ids = set()

    # ---- shared context loaded once, passed to every step ----
    with _timed(timings, "load active corpus"):
        all_docs = list_documents(paths)
        all_chunks_list = load_all_chunks(paths)
        all_pairs = _build_pairs(all_docs, all_chunks_list)
        declared: dict[str, list[str]] = {}
        for doc in all_docs:
            kw = doc.metadata.get("keywords")
            if isinstance(kw, list):
                declared[doc.id] = kw

    with _timed(timings, "embed"):
        store = _embed_chunks_incremental(all_chunks_list, paths, stale_doc_ids)

    # Mutable context dict -- steps can publish results for later waves.
    ctx: dict = dict(
        paths=paths,
        docs=all_docs,
        chunks=all_chunks_list,
        pairs=all_pairs,
        declared=declared,
        store=store,
        graph=None,  # populated by wave B
        resolve_bibliography_doi=resolve_bibliography_doi,
    )

    # ---- execute the DAG ----
    for wave_label, step_names in REFRESH_DAG:
        steps = [(_REFRESH_STEPS[name], name) for name in step_names]
        with _timed(timings, wave_label):
            if len(steps) == 1:
                steps[0][0](ctx)
            else:
                with ThreadPoolExecutor(max_workers=len(steps)) as pool:
                    futs = {pool.submit(fn, ctx): name for fn, name in steps}
                    for fut in futs:
                        fut.result()  # propagate exceptions

    _print_timings(timings, t0)


# ---------------------------------------------------------------------------
# Refresh DAG: waves and steps
# ---------------------------------------------------------------------------
# Each wave is (label, [step_names]).  Steps within a wave run in parallel.
# Waves run sequentially -- a step may depend on anything from earlier waves.
#
# To add a step: define ``_refresh_<name>(ctx)`` and register it in
# ``_REFRESH_STEPS``, then place the key in the right wave.

def _refresh_doc_edges(ctx: dict) -> None:
    populate_doc_edges(ctx["docs"], ctx["pairs"], ctx["store"])


def _refresh_topics(ctx: dict) -> None:
    vocab = extract_topics(ctx["pairs"], declared_per_doc=ctx["declared"])
    write_topics(ctx["paths"].topics_path, vocab)


def _refresh_images_index(ctx: dict) -> None:
    build_images_index(ctx["paths"], doc_ids=[d.id for d in ctx["docs"]])


def _refresh_openalex(ctx: dict) -> None:
    """Resolve citations via OpenAlex API (DOI + bulk reference expansion)."""
    if not ctx.get("resolve_bibliography_doi", False):
        return
    import asyncio

    from ..citestore import AsyncResolver, DatabaseManager

    all_cits = []
    for doc in ctx["docs"]:
        all_cits.extend(doc.citations or [])
    if not all_cits:
        return

    db_path = ctx["paths"].root / ".citestore.db"

    async def _run() -> None:
        async with DatabaseManager(db_path) as db:
            resolver = AsyncResolver(
                db,
                email="fgrillo89@gmail.com",
                expand_references=True,
            )
            try:
                # Convert to dicts for the resolver API
                cit_dicts = [c.to_dict() if hasattr(c, "to_dict") else c for c in all_cits]
                results = await resolver.resolve_batch(cit_dicts)
            finally:
                await resolver.close()

        # Map results back onto CitationEntry objects
        result_by_text: dict[str, object] = {}
        for r in results:
            if r.source_text:
                result_by_text[r.source_text] = r

        for cit in all_cits:
            raw = cit.raw_text if hasattr(cit, "raw_text") else cit.get("raw_text", "")
            r = result_by_text.get(raw)
            if r is None or r.work is None:
                continue
            w = r.work
            cit.resolution = "openalex"
            cit.title = w.title
            cit.authors = w.authors
            cit.year = w.year or cit.year
            cit.venue = w.journal
            cit.volume = w.volume
            cit.pages = (
                f"{w.first_page}--{w.last_page}".strip("-")
                if w.first_page or w.last_page
                else ""
            )
            cit.publisher = w.publisher
            cit.doi = w.doi or cit.doi

    asyncio.run(_run())


def _refresh_cite_heuristics(ctx: dict) -> None:
    """Enrich citations with heuristic parsing + DOI content negotiation."""
    from .cite_parse import enrich_citations
    enrich_citations(ctx["docs"])


def _refresh_bibliography(ctx: dict) -> None:
    write_corpus_bibliography(
        ctx["paths"],
        ctx["docs"],
        resolve_doi=ctx.get("resolve_bibliography_doi", False),
    )


def _refresh_corpus_graph(ctx: dict) -> None:
    graph = build_corpus_graph(ctx["docs"], ctx["chunks"], ctx["store"])
    write_graph(ctx["paths"], graph)
    ctx["graph"] = graph  # safe: wave B runs alone, wave C starts after barrier


def _refresh_explorer_index(ctx: dict) -> None:
    idx = build_explorer_index(
        ctx["docs"], ctx["chunks"], ctx["graph"], ctx["store"],
    )
    save_explorer_index(ctx["paths"].explorer_index_path, idx)


def _refresh_pagerank(ctx: dict) -> None:
    write_pagerank(ctx["paths"], ctx["docs"], ctx["graph"])


def _refresh_doc_resave(ctx: dict) -> None:
    _resave_docs(ctx["paths"], ctx["docs"])


_REFRESH_STEPS: dict[str, callable] = {
    "doc_edges":        _refresh_doc_edges,
    "topics":           _refresh_topics,
    "images_index":     _refresh_images_index,
    "cite_heuristics":  _refresh_cite_heuristics,
    "openalex":         _refresh_openalex,
    "bibliography":     _refresh_bibliography,
    "corpus_graph":   _refresh_corpus_graph,
    "explorer_index": _refresh_explorer_index,
    "pagerank":       _refresh_pagerank,
    "doc_resave":     _refresh_doc_resave,
}

REFRESH_DAG: list[tuple[str, list[str]]] = [
    # Wave A: independent -- only needs docs + store
    ("wave A (edges+topics+images+heuristics+openalex)", [
        "doc_edges", "topics", "images_index", "cite_heuristics", "openalex",
    ]),
    # Wave A2: bibliography needs citation enrichment to finish first
    ("wave A2 (bibliography)", [
        "bibliography",
    ]),
    # Wave B: needs doc_edges (populates doc.similar_to, .cites, .cites_same)
    ("wave B (corpus graph)", [
        "corpus_graph",
    ]),
    # Wave C: needs graph
    ("wave C (explorer+pagerank+resave)", [
        "explorer_index", "pagerank", "doc_resave",
    ]),
]


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def ingest_corpus(
    input_dir: Path,
    output_dir: Path,
    *,
    max_workers: int | None = None,
    mode: str = "additive",
    parser_backend: str = "default",
    refresh: bool = True,
    resolve_bibliography_doi: bool = False,
) -> CorpusPaths:
    """Ingest a directory of sources into a corpus bundle.

    Streaming: each source is parsed, enriched, and persisted to disk
    individually. Only lightweight ``FileReceipt`` objects are kept in
    memory, so this scales to thousands of papers without OOM.

    After all sources are persisted the manifest is saved (crash-recovery
    boundary). If ``refresh=True`` (default), corpus-wide derived
    artifacts (embeddings, graph, topics, etc.) are rebuilt via
    ``refresh_corpus()``. Pass ``refresh=False`` or use ``--no-refresh``
    from the CLI to skip.
    """
    from .manifest import SourceRecord

    validate_backend(parser_backend)

    timings: dict[str, float] = {}
    t0_run = time.monotonic()

    paths = CorpusPaths(root=output_dir)
    paths.ensure()

    # 1. Enumerate, diff, dedupe
    manifest, change_set, dedup_aliases = _prepare_change_set(
        input_dir, paths, mode, timings,
    )

    # If nothing changed but derived artifacts are missing, still run refresh.
    needs_refresh = refresh and _derived_artifacts_missing(paths)
    if change_set.is_empty and not dedup_aliases:
        if needs_refresh:
            print(
                "[ingest] sources unchanged, but derived artifacts missing "
                "-- running refresh",
                file=sys.stderr,
            )
            refresh_corpus(
                paths,
                resolve_bibliography_doi=resolve_bibliography_doi,
            )
        else:
            print(
                "[ingest] nothing to do -- corpus already contains every source",
                file=sys.stderr,
            )
        return paths

    # 2. Stream parse+persist (each file written to disk as it completes).
    #    Crash resume: if a prior run persisted artifacts but crashed before
    #    saving the manifest, those files are already on disk. We skip them
    #    and build synthetic receipts instead of re-parsing.
    receipts: list[FileReceipt] = []
    if change_set.to_parse:
        to_parse, recovered = _recover_completed(
            change_set.to_parse, paths,
        )
        receipts.extend(recovered)
        if to_parse:
            with _timed(timings, "parse+persist (streaming)"):
                receipts.extend(
                    _stream_parse_and_persist(
                        to_parse, paths, max_workers, parser_backend,
                    )
                )
        else:
            timings["parse+persist (streaming)"] = 0.0
    else:
        timings["parse+persist (streaming)"] = 0.0

    # 3. Identify stale doc_ids from replacements + deletes
    stale_doc_ids = _identify_stale_docs(
        receipts, dedup_aliases, change_set, manifest, input_dir,
    )

    # 4. Update manifest with new records + aliases
    _update_manifest(
        manifest, receipts, dedup_aliases, change_set, paths, input_dir,
    )

    # 5. Remove stale artifacts (only if no other source references them)
    still_active_doc_ids = {
        s.doc_id for s in manifest.sources.values()
        if s.status == "active"
    }
    safe_to_remove = stale_doc_ids - still_active_doc_ids
    if safe_to_remove:
        with _timed(timings, "remove stale"):
            _remove_doc_artifacts(paths, safe_to_remove)

    # 6. Save manifest (crash-recovery boundary: all per-doc artifacts
    #    are on disk, manifest is consistent. A crash during refresh
    #    loses only derived artifacts, which are fully reproducible
    #    by re-running ``refresh``.)
    manifest.last_ingest = SourceRecord.now_iso()
    manifest.save(paths.manifest_path)

    # 7. Rebuild derived artifacts
    if refresh:
        refresh_corpus(
            paths,
            stale_doc_ids=stale_doc_ids,
            resolve_bibliography_doi=resolve_bibliography_doi,
        )
    else:
        print(
            "[ingest] --no-refresh: skipping derived artifacts",
            file=sys.stderr,
        )

    # 8. Save manifest again with embedder fingerprint (after refresh)
    if refresh:
        from ..embedding import current_backend

        manifest.embedder_fingerprint = _embedder_fingerprint(
            current_backend(),
        )
        manifest.save(paths.manifest_path)

    _print_timings(timings, t0_run)
    return paths


def _remove_doc_artifacts(paths: CorpusPaths, doc_ids: set[str]) -> None:
    """Physically delete corpus artifacts for the given doc_ids.

    Removes doc JSON, chunks JSONL, markdown, and image directories
    whose sidecars belong exclusively to stale doc_ids.
    """
    import shutil

    for did in doc_ids:
        doc_json = paths.docs_dir / f"{did}.json"
        if doc_json.exists():
            doc_json.unlink()
        chunk_jsonl = paths.chunks_dir / f"{did}.jsonl"
        if chunk_jsonl.exists():
            chunk_jsonl.unlink()
        md_file = paths.markdown_dir / f"{did}.md"
        if md_file.exists():
            md_file.unlink()
        # Image dir uses image_slug(doc_id) -- try direct match first.
        img_dir = paths.images_dir / image_slug(did)
        if img_dir.is_dir():
            shutil.rmtree(img_dir)
    # Fallback: walk remaining image dirs and check sidecar doc_ids.
    # Catches dirs created by older ingest runs that used stem-based slugs.
    if paths.images_dir.exists():
        for img_dir in paths.images_dir.iterdir():
            if not img_dir.is_dir():
                continue
            sidecars = list(img_dir.glob("*.json"))
            if not sidecars:
                continue
            # Check ALL sidecars: only remove if every image belongs
            # to stale doc_ids (don't destroy a shared dir).
            all_stale = True
            for sc in sidecars:
                try:
                    data = json.loads(sc.read_text(encoding="utf-8"))
                    img_id = data.get("id", "")
                    img_did = img_id.split("/", 1)[0] if "/" in img_id else ""
                    if img_did and img_did not in doc_ids:
                        all_stale = False
                        break
                except Exception:  # noqa: BLE001
                    all_stale = False
                    break
            if all_stale:
                shutil.rmtree(img_dir)


def _build_pairs(
    docs: list[Document], chunks: list[Chunk],
) -> list[tuple[str, list[Chunk]]]:
    """Build (doc_id, chunks) pairs from the full corpus."""
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    return [(d.id, by_doc.get(d.id, [])) for d in docs]
