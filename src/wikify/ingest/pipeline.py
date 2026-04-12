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
from .bibtex import write_corpus_bibtex
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
from .parsers.registry import ParseResult, parse_file
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
class _ParsedBundle:
    """Everything a worker produces for one source file.

    Carries only picklable data so it can cross a ProcessPoolExecutor
    boundary.
    """

    src_path: str
    kind: str
    doc_id: str
    parsed: ParseResult
    chunks: list[Chunk]
    image_dir: str
    equations: list[dict]
    figure_refs: list[dict]
    parse_seconds: float = 0.0


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


def image_slug(path: Path) -> str:
    """Filesystem-safe folder name from a paper filename (<=80 chars)."""
    stem = path.stem
    slug = re.sub(r"[^\w\s-]", "", stem)
    slug = re.sub(r"\s+", "_", slug).strip("_")
    if len(slug) <= 80:
        return slug or hashlib.sha1(stem.encode("utf-8")).hexdigest()[:12]
    cut = slug[:80].rsplit("_", 1)[0]
    return cut or hashlib.sha1(stem.encode("utf-8")).hexdigest()[:12]


def _default_workers() -> int:
    cpu = os.cpu_count() or 2
    return max(2, int(cpu * 0.6))


def _parse_worker(src_str: str, images_root_str: str) -> _ParsedBundle:
    """Parse + chunk one source file. Runs in a worker process."""
    src = Path(src_str)
    images_root = Path(images_root_str)
    t_worker = time.monotonic()
    kind, parsed = parse_file(src)
    did = doc_id_for(src)

    img_slug = image_slug(src)
    image_dir_path = images_root / img_slug
    image_dir = str(image_dir_path)

    if parsed.raw_images:
        saved = save_doc_images(did, image_dir_path, parsed.raw_images)
        parsed.images.extend(saved)

    chunks = chunk_document(did, parsed.markdown, parsed.sections)
    chunks += caption_chunks_for(did, parsed.images, ord_offset=len(chunks))

    equations = extract_equations(parsed.markdown)
    figure_refs = extract_figure_refs(parsed.markdown)
    bind_equations_to_chunks(chunks, equations)

    return _ParsedBundle(
        src_path=str(src),
        kind=kind,
        doc_id=did,
        parsed=parsed,
        chunks=chunks,
        image_dir=image_dir,
        equations=equations,
        figure_refs=figure_refs,
        parse_seconds=time.monotonic() - t_worker,
    )


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
# Deduplication
# ---------------------------------------------------------------------------

def _existing_corpus_hashes(paths: CorpusPaths) -> set[str]:
    if not paths.docs_dir.exists():
        return set()
    out: set[str] = set()
    for f in paths.docs_dir.glob("*.json"):
        stem = f.stem
        if len(stem) >= 13 and stem[-13] == "_":
            out.add(stem[-12:])
    return out


def _dedupe_sources(
    raw_sources: list[Path],
    paths: CorpusPaths,
) -> tuple[list[Path], dict]:
    """Filter sources to genuinely-new files (intra-run + cross-run dedup)."""
    seen_hashes: set[str] = set()
    existing_hashes = _existing_corpus_hashes(paths)
    unique: list[Path] = []
    intra_dupe_paths: list[Path] = []
    existing_paths: list[Path] = []
    for src in raw_sources:
        try:
            h = content_hash(src)
        except OSError:
            unique.append(src)
            continue
        if h in existing_hashes:
            existing_paths.append(src)
            continue
        if h in seen_hashes:
            intra_dupe_paths.append(src)
            continue
        seen_hashes.add(h)
        unique.append(src)
    return unique, {
        "intra_dupes": len(intra_dupe_paths),
        "intra_dupe_paths": intra_dupe_paths,
        "existing": len(existing_paths),
        "existing_paths": existing_paths,
    }


# ---------------------------------------------------------------------------
# Stage: parallel parse + chunk
# ---------------------------------------------------------------------------

def _parse_sources(
    sources: list[Path],
    paths: CorpusPaths,
    max_workers: int | None,
) -> list[_ParsedBundle]:
    """Parse and chunk all sources in parallel. Returns sorted bundles."""
    workers = max_workers if max_workers is not None else _default_workers()
    bundles: list[_ParsedBundle] = []
    if workers > 1 and len(sources) > 1:
        images_root_str = str(paths.images_dir)
        print(
            f"[ingest] parsing {len(sources)} sources with {workers} workers",
            file=sys.stderr,
        )
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_parse_worker, str(src), images_root_str): src
                for src in sources
            }
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    bundles.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    print(f"[ingest] parse failed for {src.name}: {exc}", file=sys.stderr)
    else:
        for src in sources:
            try:
                bundles.append(_parse_worker(str(src), str(paths.images_dir)))
            except Exception as exc:  # noqa: BLE001
                print(f"[ingest] parse failed for {src.name}: {exc}", file=sys.stderr)

    bundles.sort(key=lambda b: b.src_path)

    # Slowest-paper report
    slow = sorted(bundles, key=lambda b: -b.parse_seconds)[:5]
    if slow and slow[0].parse_seconds > 5.0:
        print("[ingest] slowest papers (parser CPU time):", file=sys.stderr)
        for b in slow:
            name = Path(b.src_path).name[:60]
            print(f"  {b.parse_seconds:6.2f}s  {name}", file=sys.stderr)

    return bundles


# ---------------------------------------------------------------------------
# Stage: per-doc persist
# ---------------------------------------------------------------------------

def _persist_bundles(
    bundles: list[_ParsedBundle],
    paths: CorpusPaths,
) -> tuple[
    list[Document], list[Chunk], list[tuple[str, list[Chunk]]],
    dict[str, list[str]], dict[str, str],
]:
    """Persist parsed bundles. Returns (docs, chunks, pairs, keywords, markdown)."""
    docs: list[Document] = []
    all_chunks: list[Chunk] = []
    pairs: list[tuple[str, list[Chunk]]] = []
    declared: dict[str, list[str]] = {}
    raw_markdown_by_id: dict[str, str] = {}

    for bundle in bundles:
        src = Path(bundle.src_path)
        chunks = bundle.chunks
        parsed = bundle.parsed

        markdown_path = str(paths.markdown_dir / f"{bundle.doc_id}.md")
        sections = sections_from_chunks(chunks)

        near_map = link_chunks_to_images(chunks, parsed.images)
        rewrite_sidecar_near_chunks(Path(bundle.image_dir), near_map)

        doc = Document(
            id=bundle.doc_id,
            source_path=str(src),
            kind=bundle.kind,
            title=parsed.title or src.stem,
            metadata=dict(parsed.metadata),
            markdown_path=markdown_path,
            image_dir=bundle.image_dir,
            sections=sections,
            images=list(parsed.images),
            n_chunks=len(chunks),
            n_tokens=sum(len(c.text) // 4 for c in chunks),
            citations=extract_citations(parsed.markdown, bundle.doc_id),
            equations=list(bundle.equations or []),
            figure_refs=list(bundle.figure_refs or []),
        )
        write_document(paths, doc, parsed.markdown, chunks)
        raw_markdown_by_id[bundle.doc_id] = parsed.markdown
        docs.append(doc)
        all_chunks.extend(chunks)
        pairs.append((bundle.doc_id, chunks))
        if isinstance(parsed.metadata.get("keywords"), list):
            declared[bundle.doc_id] = parsed.metadata["keywords"]

    return docs, all_chunks, pairs, declared, raw_markdown_by_id


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
    """
    import numpy as np

    from ..store.vectors import load_vectors

    target_ids = [c.id for c in all_chunks]
    target_set = set(target_ids)

    # Try to load existing vectors for reuse
    reusable: dict[str, np.ndarray] = {}
    if paths.vectors_path.exists() and not stale_doc_ids == target_set:
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

    from ..embedding import current_backend

    backend = current_backend()
    meta = VectorsMeta(
        backend=str(backend["backend"]),
        dim=int(store.matrix.shape[1]) if store.matrix.size else int(backend["dim"] or 0),
        model=backend["model"],  # type: ignore[arg-type]
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
            cit_year = _safe_int(cit.get("year"))
            raw = str(cit.get("raw_text") or cit.get("title") or "")
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
    paths.pagerank_path.write_text(json.dumps(pagerank), encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage: doc resave (with populated edges)
# ---------------------------------------------------------------------------

def _resave_docs(
    paths: CorpusPaths,
    docs: list[Document],
    raw_markdown_by_id: dict[str, str],
) -> None:
    from ..store.corpus import _doc_to_dict

    for doc in docs:
        (paths.docs_dir / f"{doc.id}.json").write_text(
            json.dumps(_doc_to_dict(doc)), encoding="utf-8"
        )
        body = raw_markdown_by_id.get(doc.id)
        if body is None:
            # Unchanged doc -- read existing markdown body from disk.
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
# Pipeline entry point
# ---------------------------------------------------------------------------

def ingest_corpus(
    input_dir: Path,
    output_dir: Path,
    *,
    max_workers: int | None = None,
    mode: str = "additive",
) -> CorpusPaths:
    """Ingest a directory of sources into a corpus bundle.

    Incremental: parses only new/changed sources, then removes stale
    artifacts only for sources whose replacement parse *succeeded*,
    then rebuilds derived artifacts from the full active corpus.

    ``mode``: ``additive`` (default) or ``sync`` (removes absent sources).
    """
    from ..store.corpus import all_chunks as load_all_chunks
    from ..store.corpus import list_documents
    from .manifest import (
        CorpusManifest,
        SourceRecord,
        diff_sources,
        source_id_for,
    )

    timings: dict[str, float] = {}
    t0_run = time.monotonic()

    paths = CorpusPaths(root=output_dir)
    paths.ensure()

    # --- Load manifest and diff sources ---

    with _timed(timings, "enumerate+dedupe"):
        manifest = CorpusManifest.load(paths.manifest_path)
        raw_sources = sorted(iter_sources(input_dir))
        change_set = diff_sources(
            raw_sources, manifest, input_root=input_dir, mode=mode,
        )

        # Intra-run dedup (two files with same content)
        seen_hashes: set[str] = set()
        deduped: list[Path] = []
        for src in change_set.to_parse:
            try:
                h = content_hash(src)
            except OSError:
                deduped.append(src)
                continue
            if h in seen_hashes:
                print(f"  [skip-intra] {src.name}", file=sys.stderr)
                continue
            seen_hashes.add(h)
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

    if change_set.is_empty:
        print(
            "[ingest] nothing to do -- corpus already contains every source",
            file=sys.stderr,
        )
        return paths

    # --- 1. Parse new/changed sources FIRST (before removing anything) ---

    bundles: list[_ParsedBundle] = []
    declared: dict[str, list[str]] = {}
    raw_md: dict[str, str] = {}

    if change_set.to_parse:
        with _timed(timings, "parse+chunk"):
            bundles = _parse_sources(change_set.to_parse, paths, max_workers)

        with _timed(timings, "per-doc persist"):
            _, _, _, declared, raw_md = _persist_bundles(bundles, paths)
    else:
        timings["parse+chunk"] = 0.0
        timings["per-doc persist"] = 0.0

    # --- 2. Determine which replacements succeeded ---
    # A replacement succeeded if a bundle was produced for that source_id.
    # If parsing failed, the old doc stays active.

    parsed_sids: set[str] = set()
    for bundle in bundles:
        sid = change_set.path_to_sid.get(bundle.src_path)
        if sid is None:
            sid = source_id_for(Path(bundle.src_path), input_dir)
        parsed_sids.add(sid)

    stale_doc_ids: set[str] = set()

    # Only remove old artifacts for replacements that produced a bundle
    for sid, old_doc_id in change_set.to_replace.items():
        if sid in parsed_sids:
            stale_doc_ids.add(old_doc_id)
            print(f"  [replace] {sid}: old {old_doc_id}", file=sys.stderr)
        else:
            print(
                f"  [replace-skipped] {sid}: parse failed, keeping {old_doc_id}",
                file=sys.stderr,
            )

    # Sync deletes: remove absent sources
    for sid in change_set.to_delete:
        rec = manifest.sources.get(sid)
        if rec and rec.status == "active":
            stale_doc_ids.add(rec.doc_id)
            manifest.sources[sid].status = "deleted"
            print(f"  [delete] {sid} ({rec.doc_id})", file=sys.stderr)

    # --- 3. Remove stale artifacts ---

    if stale_doc_ids:
        with _timed(timings, "remove stale"):
            _remove_doc_artifacts(paths, stale_doc_ids)

    # --- 4. Update manifest with successfully parsed sources ---

    for bundle in bundles:
        sid = change_set.path_to_sid.get(bundle.src_path)
        if sid is None:
            sid = source_id_for(Path(bundle.src_path), input_dir)
        h = bundle.doc_id.rsplit("_", 1)[-1]
        rec = SourceRecord(
            source_id=sid,
            source_path=bundle.src_path,
            content_hash=h,
            doc_id=bundle.doc_id,
            status="active",
            chunk_ids=[c.id for c in bundle.chunks],
            parsed_at=SourceRecord.now_iso(),
        )
        manifest.sources[sid] = rec

    # --- 5. Derived rebuild (full active corpus on disk) ---

    with _timed(timings, "load active corpus"):
        all_docs = list_documents(paths)
        all_chunks_list = load_all_chunks(paths)
        all_pairs = _build_pairs(all_docs, all_chunks_list)
        for doc in all_docs:
            if doc.id not in declared and isinstance(
                doc.metadata.get("keywords"), list
            ):
                declared[doc.id] = doc.metadata["keywords"]

    with _timed(timings, "embed"):
        store = _embed_chunks_incremental(
            all_chunks_list, paths, stale_doc_ids,
        )

    with _timed(timings, "doc edges"):
        populate_doc_edges(all_docs, all_pairs, store)

    with _timed(timings, "corpus graph"):
        graph = build_corpus_graph(all_docs, all_chunks_list, store)
        write_graph(paths, graph)

    with _timed(timings, "explorer index"):
        idx = build_explorer_index(all_docs, all_chunks_list, graph, store)
        save_explorer_index(paths.explorer_index_path, idx)

    with _timed(timings, "pagerank"):
        write_pagerank(paths, all_docs, graph)

    with _timed(timings, "topics"):
        vocab = extract_topics(all_pairs, declared_per_doc=declared)
        write_topics(paths.topics_path, vocab)

    with _timed(timings, "image index"):
        build_images_index(paths, doc_ids=[d.id for d in all_docs])

    with _timed(timings, "bibtex"):
        write_corpus_bibtex(paths, all_docs)

    with _timed(timings, "doc resave"):
        _resave_docs(paths, all_docs, raw_md)

    # --- 6. Save manifest ---
    manifest.last_ingest = SourceRecord.now_iso()
    from ..embedding import current_backend

    backend = current_backend()
    manifest.embedder_fingerprint = (
        f"{backend['backend']}:{backend.get('model', '')}:"
        f"{backend.get('dim', '')}"
    )
    manifest.save(paths.manifest_path)

    _print_timings(timings, t0_run)
    return paths


def _remove_doc_artifacts(paths: CorpusPaths, doc_ids: set[str]) -> None:
    """Physically delete corpus artifacts for the given doc_ids.

    Removes doc JSON, chunks JSONL, markdown, and image directory so
    they are not picked up by ``list_documents`` / ``all_chunks`` during
    the derived rebuild.
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
    # Image dirs use slugified names, not doc_ids directly.  Walk the
    # image folder and check sidecar JSON for matching doc_ids.
    if paths.images_dir.exists():
        for img_dir in paths.images_dir.iterdir():
            if not img_dir.is_dir():
                continue
            # Check if any sidecar in this dir belongs to a stale doc
            sidecars = list(img_dir.glob("*.json"))
            if not sidecars:
                continue
            try:
                first = json.loads(sidecars[0].read_text(encoding="utf-8"))
                img_id = first.get("id", "")
                img_doc_id = img_id.split("/", 1)[0] if "/" in img_id else ""
                if img_doc_id in doc_ids:
                    shutil.rmtree(img_dir)
            except Exception:  # noqa: BLE001
                pass


def _build_pairs(
    docs: list[Document], chunks: list[Chunk],
) -> list[tuple[str, list[Chunk]]]:
    """Build (doc_id, chunks) pairs from the full corpus."""
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    return [(d.id, by_doc.get(d.id, [])) for d in docs]


# ---------------------------------------------------------------------------
# Backwards-compatible aliases for test imports
# ---------------------------------------------------------------------------
# These were private helpers in refresh.py that tests import directly.
# Keep them available during migration.
_sections_from_chunks = sections_from_chunks
_bind_equations_to_chunks = bind_equations_to_chunks
_doc_id_for = doc_id_for
_content_hash = content_hash
_image_slug = image_slug
_iter_sources = iter_sources
_write_pagerank = write_pagerank
_populate_doc_edges = populate_doc_edges
_dedupe_sources_compat = _dedupe_sources
