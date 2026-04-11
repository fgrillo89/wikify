"""Single entry point: ``ingest_corpus(input_dir, output_dir)``.

Walks an input directory, parses every supported file, chunks it, embeds
chunks + image captions, materialises the corpus graph, extracts the
topic vocabulary, and writes everything under ``output_dir``. Idempotent
modulo file content (file hash → doc id).
"""

import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

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
from .equations import extract_equations
from .figure_refs import extract_figure_refs
from .images import (
    caption_chunks_for,
    link_chunks_to_images,
    rewrite_sidecar_near_chunks,
    save_doc_images,
)
from .parsers.registry import ParseResult, parse_file
from .sampler_index import build_sampler_index, save_sampler_index
from .topics import extract_topics, write_topics


@dataclass
class _ParsedBundle:
    """Everything a worker produces for one source file.

    Carries only picklable data so it can cross a ProcessPoolExecutor
    boundary. The main process then runs the sequential persist phase
    (write_document, embeddings, graph, ...).
    """

    src_path: str
    kind: str
    doc_id: str
    parsed: ParseResult
    chunks: list[Chunk]
    image_dir: str
    equations: list[dict]
    figure_refs: list[dict]
    parse_seconds: float = 0.0  # wall time spent in this worker (profiling)


def _default_workers() -> int:
    """Return 60 percent of CPU cores, minimum 2.

    Mirrors the archived ``wikify.ingest.service.default_workers`` policy.
    PDF parsing (pymupdf + pymupdf4llm) is CPU-bound, so a process pool
    scales near-linearly up to physical core count; 60 % leaves headroom
    for the OS and the embedder that runs after the parse fan-in.
    """
    cpu = os.cpu_count() or 2
    return max(2, int(cpu * 0.6))


def _parse_worker(src_str: str, images_root_str: str) -> _ParsedBundle:
    """Parse + chunk one source file. Runs in a worker process.

    Writes image binaries into the per-doc image directory (disjoint
    between workers, so no contention). Returns a bundle the main process
    consumes sequentially in the persist phase.
    """
    src = Path(src_str)
    images_root = Path(images_root_str)
    t_worker = time.monotonic()
    kind, parsed = parse_file(src)
    doc_id = _doc_id_for(src)

    # Image folder uses a clean human-readable slug (word-bounded,
    # no hash, ≤80 chars) so on-disk paths stay well under Windows
    # MAX_PATH and are easy to inspect. doc_id (with hash) remains
    # the corpus index key; image_dir is the bucket on disk.
    image_slug = _image_slug(src)
    image_dir_path = images_root / image_slug
    # Store as absolute path so read_doc_images works regardless of
    # the caller's cwd. Corpora are not relocatable today; if that
    # changes, swap to a path relative to corpus root.
    image_dir = str(image_dir_path)

    # Persist images BEFORE caption chunking so parsed.images is
    # populated when caption_chunks_for reads it. Without this
    # ordering, PDF caption chunks were silently empty (PDF parsers
    # return ``images=[]`` and only expose raw blobs via
    # ``metadata['_raw_images']``).
    raw_images = parsed.metadata.pop("_raw_images", None)
    if raw_images:
        saved = save_doc_images(doc_id, image_dir_path, raw_images)
        parsed.images.extend(saved)

    chunks = chunk_document(doc_id, parsed.markdown, parsed.sections)
    chunks += caption_chunks_for(doc_id, parsed.images, ord_offset=len(chunks))

    # Extract equations and figure refs from the cleaned markdown. Both
    # are pure functions of the markdown so they're computed in the
    # worker process. Equation→chunk binding happens in the main process
    # because it requires the final chunk list (the worker already has
    # it but binding here keeps the worker output picklable as plain
    # dicts and the binding logic in one place).
    equations = extract_equations(parsed.markdown)
    figure_refs = extract_figure_refs(parsed.markdown)
    _bind_equations_to_chunks(chunks, equations)

    return _ParsedBundle(
        src_path=str(src),
        kind=kind,
        doc_id=doc_id,
        parsed=parsed,
        chunks=chunks,
        image_dir=image_dir,
        equations=equations,
        figure_refs=figure_refs,
        parse_seconds=time.monotonic() - t_worker,
    )


def _bind_equations_to_chunks(chunks: list[Chunk], equations: list[dict]) -> None:
    """Attach equation ids to the chunks whose char_span contains them.

    Each chunk's ``equation_ids`` becomes the ordered list of equation
    ids whose ``char_offset`` falls inside the chunk's ``[start, end)``
    span. Caption chunks (``__image__`` section path) carry char_span
    ``(0, len(text))`` because they were synthesised, not sliced from
    the body — they get no equations.
    """
    if not equations:
        return
    # Sort spans for a quick scan: chunks are typically already in
    # ord-order matching their char_span order.
    body_chunks = [c for c in chunks if not (c.section_path and c.section_path[0] == "__image__")]
    body_chunks.sort(key=lambda c: c.char_span[0])
    if not body_chunks:
        return
    for eq in equations:
        offset = int(eq.get("char_offset") or 0)
        # Linear scan is fine for the few-hundred chunks per doc we see;
        # bisect would matter at thousands.
        for c in body_chunks:
            start, end = c.char_span
            if start <= offset < end:
                c.equation_ids.append(eq["id"])
                break


def ingest_corpus(
    input_dir: Path,
    output_dir: Path,
    *,
    max_workers: int | None = None,
) -> CorpusPaths:
    """Ingest a directory of sources into a corpus bundle.

    ``max_workers`` controls the parse/chunk parallelism. ``None`` picks
    60 % of available CPU cores (min 2). Pass ``1`` to force serial
    execution (useful for debugging and single-file runs).

    **Idempotent and deduplicating.** The same source file (same content
    hash) is never processed twice — neither within a single run (two
    copies of the same paper under different filenames in ``input_dir``)
    nor across runs (re-ingesting into a corpus that already has the
    paper). Dedup is by sha1 of the file bytes, which is the same hash
    used to build ``doc_id``. Skipped files are reported on stderr but
    do not error.
    """
    timings: dict[str, float] = {}
    t0_run = time.monotonic()

    paths = CorpusPaths(root=output_dir)
    paths.ensure()

    # Stage 1: enumerate, hash, dedupe sources -----------------------
    t = time.monotonic()
    raw_sources = sorted(_iter_sources(input_dir))
    sources, dedup_report = _dedupe_sources(raw_sources, paths)
    timings["enumerate+dedupe"] = time.monotonic() - t

    if dedup_report["intra_dupes"] or dedup_report["existing"]:
        print(
            f"[ingest] dedupe: {dedup_report['intra_dupes']} intra-run "
            f"duplicates, {dedup_report['existing']} already in corpus, "
            f"{len(sources)} unique to ingest",
            file=sys.stderr,
        )
    for path in dedup_report["intra_dupe_paths"]:
        print(f"  [skip-intra] {path.name}", file=sys.stderr)
    for path in dedup_report["existing_paths"]:
        print(f"  [skip-existing] {path.name}", file=sys.stderr)

    if not sources:
        print("[ingest] nothing to do — corpus already contains every source", file=sys.stderr)
        return paths

    # Stage 2: parse + chunk in parallel -----------------------------
    t = time.monotonic()
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
                pool.submit(_parse_worker, str(src), images_root_str): src for src in sources
            }
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    bundles.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[ingest] parse failed for {src.name}: {exc}",
                        file=sys.stderr,
                    )
    else:
        for src in sources:
            try:
                bundles.append(_parse_worker(str(src), str(paths.images_dir)))
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[ingest] parse failed for {src.name}: {exc}",
                    file=sys.stderr,
                )

    # Preserve sorted-source order so downstream artifacts (embeddings,
    # graph, tests) are deterministic regardless of worker completion
    # order.
    bundles.sort(key=lambda b: b.src_path)
    timings["parse+chunk (parallel)"] = time.monotonic() - t

    # Slowest-paper report: helps identify outliers that drag wall time
    # in the parallel pool. If one paper takes 60 s and the other 49
    # finish in 5 s each, the pool waits 60 s on the slow one.
    slow = sorted(bundles, key=lambda b: -b.parse_seconds)[:5]
    if slow and slow[0].parse_seconds > 5.0:
        print("[ingest] slowest papers (parser CPU time):", file=sys.stderr)
        for b in slow:
            name = Path(b.src_path).name[:60]
            print(f"  {b.parse_seconds:6.2f}s  {name}", file=sys.stderr)

    # Stage 3: per-doc persist (write_document, link_chunks_to_images,
    # extract_citations) ----------------------------------------------
    t = time.monotonic()
    docs: list[Document] = []
    all_chunks_list: list[Chunk] = []
    docs_chunks_pairs: list[tuple[str, list[Chunk]]] = []
    declared: dict[str, list[str]] = {}
    raw_markdown_by_id: dict[str, str] = {}

    for bundle in bundles:
        src = Path(bundle.src_path)
        kind = bundle.kind
        doc_id = bundle.doc_id
        parsed = bundle.parsed
        chunks = bundle.chunks
        image_dir = bundle.image_dir

        markdown_path = str(paths.markdown_dir / f"{doc_id}.md")
        sections = _sections_from_chunks(chunks)

        # Populate near_chunk_ids: for each image, list the body chunks
        # whose prose mentions it via "Fig. N", "Figure 2a", "Table 3", or
        # "Scheme 4" inline references. This was a long-standing dead
        # field — sidecars and doc.json round-tripped an empty list, the
        # ImageRecord/ImageRef projections didn't even include the field.
        # Now distill handlers can know which chunks discuss which figure.
        near_map = link_chunks_to_images(chunks, parsed.images)
        rewrite_sidecar_near_chunks(Path(image_dir), near_map)

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
            equations=list(bundle.equations or []),
            figure_refs=list(bundle.figure_refs or []),
        )
        write_document(paths, doc, parsed.markdown, chunks)
        raw_markdown_by_id[doc_id] = parsed.markdown
        docs.append(doc)
        all_chunks_list.extend(chunks)
        docs_chunks_pairs.append((doc_id, chunks))
        if isinstance(parsed.metadata.get("keywords"), list):
            declared[doc_id] = parsed.metadata["keywords"]
    timings["per-doc persist"] = time.monotonic() - t

    # Stage 4: embed --------------------------------------------------
    t = time.monotonic()
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
    timings["embed"] = time.monotonic() - t

    # Stage 5: doc-level edges (similar_to, cites, cites_same) -------
    # Must run BEFORE build_corpus_graph so the graph picks up the
    # resolved cross-paper citation edges. (Pre-fix this ran later and
    # the saved graph.json had silently empty "cites" edges, which
    # cascaded into pagerank and the distill sampler.)
    t = time.monotonic()
    _populate_doc_edges(docs, docs_chunks_pairs, store)
    timings["doc edges"] = time.monotonic() - t

    # Stage 6: build corpus graph -----------------------------------
    t = time.monotonic()
    graph = build_corpus_graph(docs, all_chunks_list, store)
    write_graph(paths, graph)
    timings["corpus graph"] = time.monotonic() - t

    # Stage 7: sampler index ----------------------------------------
    t = time.monotonic()
    sampler_idx = build_sampler_index(docs, all_chunks_list, graph, store)
    save_sampler_index(paths.sampler_index_path, sampler_idx)
    timings["sampler index"] = time.monotonic() - t

    # Stage 8: pagerank ---------------------------------------------
    t = time.monotonic()
    _write_pagerank(paths, docs, graph)
    timings["pagerank"] = time.monotonic() - t

    # Stage 9: topics, image index, bibtex --------------------------
    t = time.monotonic()
    vocab = extract_topics(docs_chunks_pairs, declared_per_doc=declared)
    write_topics(paths.topics_path, vocab)
    timings["topics"] = time.monotonic() - t

    t = time.monotonic()
    build_images_index(paths, doc_ids=[d.id for d in docs])
    timings["image index"] = time.monotonic() - t

    t = time.monotonic()
    write_corpus_bibtex(paths, docs)
    timings["bibtex"] = time.monotonic() - t

    # Stage 10: re-save docs with populated edges -------------------
    t = time.monotonic()
    import json as _json

    from ..store.corpus import _doc_to_dict  # internal helper reuse

    for doc in docs:
        (paths.docs_dir / f"{doc.id}.json").write_text(
            _json.dumps(_doc_to_dict(doc)), encoding="utf-8"
        )
        body = raw_markdown_by_id.get(doc.id, "")
        write_doc_markdown(paths, doc, body)
    timings["doc resave"] = time.monotonic() - t

    # Print the stage timing report.
    total = time.monotonic() - t0_run
    print("[ingest] timing report:", file=sys.stderr)
    for stage, secs in timings.items():
        pct = 100.0 * secs / total if total > 0 else 0.0
        print(f"  {stage:30}  {secs:7.2f}s  ({pct:5.1f}%)", file=sys.stderr)
    print(f"  {'TOTAL':30}  {total:7.2f}s", file=sys.stderr)

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

    # Use the same cosine threshold as the corpus graph's doc_similar
    # edges (config.DOC_SIM_COS) so doc.similar_to and the graph agree
    # on what counts as "similar". Previously this used a hardcoded 0.7
    # while corpus_graph used 0.75 — papers could be in one set but not
    # the other.
    from .config import DOC_SIM_COS

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

    # 2. cites: resolve each citation to a corpus doc using a year-bucketed
    #    fuzzy match (ported from the archived ``wikify.ingest.extract.
    #    cite_match`` pattern). Year filter is O(1), then we score each
    #    same-year candidate by surname hit + title-word overlap. Much
    #    more tolerant than the previous exact-normalized-title match —
    #    small punctuation differences used to drop all citations on the
    #    floor.
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


def _title_word_set(title: str, min_len: int = 4) -> set[str]:
    """Meaningful words from a title, lowercased, at least ``min_len`` chars.

    Used by the cite_match scorer to measure title-word overlap between a
    raw citation string and a corpus-doc title. Short words (``a``, ``of``,
    ``the``) are dropped because every citation would match them.
    """
    return {w for w in _normalize_title(title).split() if len(w) >= min_len}


def _safe_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iter_sources(root: Path):
    exts = {".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".html", ".htm"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def _doc_id_for(path: Path) -> str:
    h = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return f"{path.stem}_{h}"


def _content_hash(path: Path) -> str:
    """Stable 12-char sha1 prefix of a file's bytes — same prefix as in
    ``_doc_id_for``. Used by the dedup pass to recognize when two source
    files contain the same paper (different filenames, same content) or
    when an incremental ingest re-encounters an already-stored doc."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _existing_corpus_hashes(paths: CorpusPaths) -> set[str]:
    """Return the set of content hashes already represented in the
    target corpus, by reading every ``docs/*.json`` and pulling the
    12-char suffix off the doc id. Cheap (just a directory walk + the
    last 12 chars of each filename), no JSON parse needed."""
    if not paths.docs_dir.exists():
        return set()
    out: set[str] = set()
    for f in paths.docs_dir.glob("*.json"):
        # filename = "{stem}_{12-char-hash}.json"
        stem = f.stem
        if len(stem) >= 13 and stem[-13] == "_":
            out.add(stem[-12:])
    return out


def _dedupe_sources(
    raw_sources: list[Path],
    paths: CorpusPaths,
) -> tuple[list[Path], dict]:
    """Filter the source list down to genuinely-new files.

    Returns ``(unique_sources, report)`` where ``report`` carries
    counts and the actual paths skipped, so the CLI can show them.
    Two dedup axes:

    1. **Intra-run**: two files in ``raw_sources`` with the same content
       hash (same paper under different filenames in ``input_dir``).
       Keep the first sorted occurrence; report the rest.
    2. **Cross-run / incremental**: a file whose content hash matches a
       doc id already saved in ``paths.docs_dir``. Skipped silently in
       the unique list but reported in the count.
    """
    seen_hashes: set[str] = set()
    existing_hashes = _existing_corpus_hashes(paths)
    unique: list[Path] = []
    intra_dupe_paths: list[Path] = []
    existing_paths: list[Path] = []
    for src in raw_sources:
        try:
            h = _content_hash(src)
        except OSError:
            # Unreadable file: pass through to the parser; it will
            # surface its own error.
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
    report = {
        "intra_dupes": len(intra_dupe_paths),
        "intra_dupe_paths": intra_dupe_paths,
        "existing": len(existing_paths),
        "existing_paths": existing_paths,
    }
    return unique, report


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


def _write_pagerank(paths: CorpusPaths, docs: list[Document], graph) -> None:
    """Compute real PageRank on the doc graph and persist to pagerank.json.

    Uses every doc-level edge kind: cites (directed citations), doc_similar
    (mean-pooled embedding similarity), and cites_same (bibliographic
    coupling). Each edge contributes weight 1.0 — papers linked by
    multiple kinds get a proportionally larger boost.
    """
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
