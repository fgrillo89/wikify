"""Read-only corpus query helpers — the surface ``cli/corpus.py`` calls.

Wraps the existing fluent KG (``corpus/graph.py``), the document
sampler (``corpus/sampling.py``), and the on-disk corpus loaders
(``corpus/chunks.py``)
into one cohesive module that the CLI can drive without sprinkling
imports across handlers.

Handle grammar (used by ``corpus show <handle>``)::

    doc:<doc_id>          full id
    doc:<short>           hash-suffix or unique suffix; resolved against the corpus
    chunk:<chunk_id>      same rules

See ``corpus/handles.py`` for the resolution semantics.
"""

from __future__ import annotations

import re

from ..api import Corpus
from ..models import Chunk, Document
from .chunks import (
    all_chunks,
    list_documents,
    read_chunks,
    read_knowledge_graph,
    read_vector_store,
)
from .handles import HandleNotFoundError
from .handles import resolve as resolve_short

# ---------------------------------------------------------------- listing


def list_doc_ids(corpus: Corpus) -> list[str]:
    """Return every document id in the corpus, sorted."""
    return sorted(d.id for d in list_documents(corpus))


def list_chunks_for_doc(corpus: Corpus, doc_id: str) -> list[Chunk]:
    """Return the chunks for one document, in original order."""
    return read_chunks(corpus, doc_id)


def list_files(corpus: Corpus) -> list[str]:
    """Return on-disk filenames under the corpus root, relative to root."""
    out: list[str] = []
    for p in sorted(corpus.root.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(corpus.root)))
    return out


# ------------------------------------------------------------------- show


def get_doc(corpus: Corpus, doc_id: str) -> Document | None:
    """Return the ``Document`` record for *doc_id* or ``None``.

    *doc_id* may be the full id or a short / suffix form; see
    :func:`resolve_doc_id`. Returns ``None`` only when no candidate
    matches; ambiguous matches raise ``AmbiguousHandleError`` so callers
    surface the conflict.
    """
    docs = list_documents(corpus)
    try:
        full = resolve_short(doc_id, (d.id for d in docs))
    except HandleNotFoundError:
        return None
    for d in docs:
        if d.id == full:
            return d
    return None


def get_chunk(corpus: Corpus, chunk_id: str) -> Chunk | None:
    """Return the ``Chunk`` for *chunk_id* or ``None``.

    Resolves short / suffix handles via :func:`resolve_short`. Ambiguous
    matches raise ``AmbiguousHandleError``.
    """
    chunks = all_chunks(corpus)
    try:
        full = resolve_short(chunk_id, (c.id for c in chunks))
    except HandleNotFoundError:
        return None
    for c in chunks:
        if c.id == full:
            return c
    return None


def resolve_doc_id(corpus: Corpus, short: str) -> str:
    """Resolve a short or full doc handle to the canonical full id.

    Raises ``HandleNotFoundError`` or ``AmbiguousHandleError``.
    """
    return resolve_short(short, (d.id for d in list_documents(corpus)))


def resolve_chunk_id(corpus: Corpus, short: str) -> str:
    """Resolve a short or full chunk handle to the canonical full id."""
    return resolve_short(short, (c.id for c in all_chunks(corpus)))


def resolve_figure_id(corpus: Corpus, short: str) -> str:
    """Resolve a short or full figure handle to the canonical full id."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    candidates = (
        nid for nid, attrs in kg._backend.G.nodes(data=True)
        if attrs.get("type") == "figure"
    )
    return resolve_short(short, candidates)


def resolve_equation_id(corpus: Corpus, short: str) -> str:
    """Resolve a short or full equation handle to the canonical full id."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    candidates = (
        nid for nid, attrs in kg._backend.G.nodes(data=True)
        if attrs.get("type") == "equation"
    )
    return resolve_short(short, candidates)


def _normalize_corpus_path(corpus: Corpus, raw: str) -> str:
    """Make a corpus-relative, forward-slash path from a stored figure path.

    Some ingest paths store absolute paths with backslashes; this returns
    a clean ``images/<slug>/<stem>.png``-style relative path when the
    raw path lives under ``corpus.root``. Falls back to the raw value
    otherwise.
    """
    if not raw:
        return ""
    norm = raw.replace("\\", "/")
    try:
        from pathlib import Path

        rp = Path(raw).resolve()
        rel = rp.relative_to(corpus.root.resolve())
        return str(rel).replace("\\", "/")
    except (ValueError, OSError):
        return norm


def get_figure(corpus: Corpus, fig_id: str) -> dict | None:
    """Return ``{id, source_id, caption, page, path, near_chunk_ids}`` or None."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    g = kg._backend.G
    try:
        full = resolve_short(
            fig_id,
            (nid for nid, a in g.nodes(data=True) if a.get("type") == "figure"),
        )
    except HandleNotFoundError:
        return None
    attrs = g.nodes[full]
    return {
        "id": full,
        "source_id": attrs.get("source_id", ""),
        "caption": attrs.get("caption", "") or "",
        "page": attrs.get("page"),
        "path": _normalize_corpus_path(corpus, str(attrs.get("path", "") or "")),
        "near_chunk_ids": list(attrs.get("near_chunk_ids", []) or []),
    }


def resolve_author_key(corpus: Corpus, short: str) -> str:
    """Resolve a short or full author key.

    Author keys are lowercase ``"first last"`` strings. Exact match wins;
    otherwise case-insensitive unique prefix is accepted (e.g.
    ``"sungjun"`` -> ``"sungjun kim"`` if unique).
    """
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    keys = [
        nid for nid, a in kg._backend.G.nodes(data=True)
        if a.get("type") == "author"
    ]
    # Accept both ``"first last"`` and pipe-safe ``"first_last"`` forms.
    short_l = short.lower().replace("_", " ")
    # Exact match first.
    for k in keys:
        if k == short_l:
            return k
    # Case-insensitive prefix.
    matches = [k for k in keys if k.startswith(short_l)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        from .handles import AmbiguousHandleError

        raise AmbiguousHandleError(short, matches)
    from .handles import HandleNotFoundError

    raise HandleNotFoundError(short)


def get_author(corpus: Corpus, key: str) -> dict | None:
    """Return ``{key, name, h_index, citation_count, n_papers, top_coauthors}``."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    g = kg._backend.G
    try:
        full = resolve_author_key(corpus, key)
    except HandleNotFoundError:
        return None
    attrs = g.nodes[full]
    sources = kg.author(full).sources()
    coauthors_qb = kg.author(full).coauthors()
    coauthor_rows = []
    for cid in coauthors_qb.ids():
        ca = g.nodes.get(cid, {})
        coauthor_rows.append({
            "key": cid,
            "name": ca.get("display_name", "") or ca.get("name", "") or "",
            "h_index": int(ca.get("h_index", 0) or 0),
            "citation_count": int(ca.get("citation_count", 0) or 0),
        })
    coauthor_rows.sort(key=lambda r: (-r["h_index"], -r["citation_count"], r["key"]))
    return {
        "key": full,
        "name": attrs.get("display_name", "") or attrs.get("name", "") or "",
        "h_index": int(attrs.get("h_index", 0) or 0),
        "citation_count": int(attrs.get("citation_count", 0) or 0),
        "n_papers": sources.count(),
        "top_coauthors": coauthor_rows[:5],
    }


def rank_authors(
    corpus: Corpus,
    *,
    by: str,
    top_k: int = 8,
) -> list[dict]:
    """Top-K authors by h_index, citation_count, or n_papers."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    g = kg._backend.G
    rows: list[dict] = []
    for nid, attrs in g.nodes(data=True):
        if attrs.get("type") != "author":
            continue
        n_papers = len(kg._backend._sources_of.get(nid, set()))
        rows.append({
            "key": nid,
            "name": attrs.get("display_name", "") or attrs.get("name", "") or "",
            "h_index": int(attrs.get("h_index", 0) or 0),
            "citation_count": int(attrs.get("citation_count", 0) or 0),
            "n_papers": n_papers,
        })
    if by not in {"h_index", "citation_count", "n_papers"}:
        raise ValueError(
            f"unknown author rank metric {by!r}; expected "
            f"h_index | citation_count | n_papers"
        )
    rows.sort(key=lambda r: (-r[by], -r["h_index"], -r["citation_count"], r["key"]))
    return rows[:top_k]


def search_authors(
    corpus: Corpus,
    query: str,
    *,
    top_k: int = 8,
    chunk_pool: int | None = None,
) -> list[dict]:
    """Authors whose papers' chunks best match *query*, ranked by best chunk."""
    pool = chunk_pool or max(top_k * 10, 50)
    hits = search_chunks(corpus, query, top_k=pool)
    if not hits:
        return []
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    g = kg._backend.G
    authors_of = kg._backend._authors_of
    grouped: dict[str, dict] = {}
    for hit in hits:
        doc_id = str(hit.get("doc_id") or hit.get("source_id") or "")
        if not doc_id:
            continue
        score = float(hit.get("score", 0.0) or 0.0)
        for author_key in authors_of.get(doc_id, set()):
            entry = grouped.setdefault(
                author_key,
                {
                    "key": author_key,
                    "name": (
                        g.nodes.get(author_key, {}).get("display_name", "")
                        or g.nodes.get(author_key, {}).get("name", "")
                        or ""
                    ),
                    "best_score": score,
                    "n_papers": 0,
                    "h_index": int(g.nodes.get(author_key, {}).get("h_index", 0) or 0),
                    "citation_count": int(
                        g.nodes.get(author_key, {}).get("citation_count", 0) or 0
                    ),
                    "_doc_ids": set(),
                },
            )
            entry["_doc_ids"].add(doc_id)
            if score > float(entry["best_score"]):
                entry["best_score"] = score
    for entry in grouped.values():
        entry["n_papers"] = len(entry.pop("_doc_ids"))
    return sorted(
        grouped.values(),
        key=lambda r: (-float(r["best_score"]), -int(r["h_index"]), str(r["key"])),
    )[:top_k]


def get_equation(corpus: Corpus, eq_id: str) -> dict | None:
    """Return ``{id, source_id, latex, label, kind, is_chemical}`` or None."""
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    g = kg._backend.G
    try:
        full = resolve_short(
            eq_id,
            (nid for nid, a in g.nodes(data=True) if a.get("type") == "equation"),
        )
    except HandleNotFoundError:
        return None
    attrs = g.nodes[full]
    return {
        "id": full,
        "source_id": attrs.get("source_id", ""),
        "latex": attrs.get("latex", "") or "",
        "label": attrs.get("label", "") or "",
        "kind": attrs.get("kind", "") or "",
        "is_chemical": bool(attrs.get("is_chemical", False)),
    }


def parse_handle(handle: str) -> tuple[str, str]:
    """Split a ``kind:id`` handle. Raise ``ValueError`` if malformed.

    Trims surrounding whitespace (and ``\\r`` from Windows ``\\r\\n``
    line endings), so handles produced by ``--format quiet`` survive
    being piped through ``xargs`` on any platform.
    """
    handle = handle.strip()
    if ":" not in handle:
        raise ValueError(
            f"handle must be 'kind:id' (e.g. 'doc:5f92b0389ccd', "
            f"'chunk:499c6728', 'figure:5f92.../fig_002', "
            f"'author:sungjun_kim'); got {handle!r}"
        )
    kind, _, ident = handle.partition(":")
    return kind, ident.strip()


# ------------------------------------------------------------------- find


def search_chunks(
    corpus: Corpus,
    query: str,
    *,
    top_k: int = 8,
    rank: str = "semantic",
) -> list[dict]:
    """Chunk search; backend selected by ``WIKIFY_QUERY_BACKEND``.

    Each result has ``id``, ``score``, and ``doc_id``. ``rank`` is one of
    ``semantic`` (cosine over chunk embeddings; legacy default),
    ``bm25`` (FTS5 — sqlite backend only), or ``hybrid`` (RRF over
    BM25 + vector — sqlite backend only).
    """
    from .store.routing import is_sqlite

    if is_sqlite():
        return _search_chunks_sqlite(corpus, query, top_k=top_k, rank=rank)
    if rank in _LEXICAL_RANKS:
        raise QueryError(
            "backend_required",
            f"--rank {rank} requires WIKIFY_QUERY_BACKEND=sqlite",
        )
    vs = read_vector_store(corpus)
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for

    meta = read_meta(corpus.vectors_path)
    embed = (
        embedder_for(meta.backend, meta.model, mode="query") if meta else None
    )
    kg = read_knowledge_graph(corpus, vectors=vs, embed_fn=embed)
    return list(kg.chunks().search(query, top_k=top_k))


def _search_chunks_sqlite(
    corpus: Corpus, query: str, *, top_k: int, rank: str,
) -> list[dict]:
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for
    from .store.routing import active_space_id, open_store

    store = open_store(corpus.root)
    try:
        if rank in {"bm25", "hybrid"}:
            if rank == "bm25":
                hits = store.search_chunks_bm25(query, top_k=top_k)
            else:
                meta = read_meta(corpus.vectors_path)
                embed = (
                    embedder_for(meta.backend, meta.model, mode="query")
                    if meta else None
                )
                qv = embed([query])[0] if embed else None  # type: ignore[index]
                space_id = active_space_id(store)
                hits = store.search_hybrid(
                    query, query_vec=qv, space_id=space_id, top_k=top_k,
                )
        else:
            # default semantic: cosine over the active embedding space.
            meta = read_meta(corpus.vectors_path)
            embed = (
                embedder_for(meta.backend, meta.model, mode="query")
                if meta else None
            )
            if embed is None:
                return []
            qv = embed([query])[0]  # type: ignore[index]
            space_id = active_space_id(store)
            if not space_id:
                return []
            hits = store.vector_index(space_id).search(qv, top_k=top_k)
        out: list[dict] = []
        for cid, score in hits:
            row = store.get_chunk(cid)
            if not row:
                continue
            out.append({
                "id": cid,
                "doc_id": row["doc_id"],
                "score": float(score),
            })
        return out
    finally:
        store.close()


def search_papers_by_title(
    corpus: Corpus,
    query: str,
    *,
    top_k: int | None = 8,
) -> list[dict]:
    """Title-only paper search: literal substring on ``Document.title``.

    Use when the user wants "papers about X in the title" rather than
    "papers whose body discusses X" (which is what the chunk-aggregated
    :func:`search_papers` returns). Case-insensitive substring; rows
    sorted by leftmost match offset, then by title length, then by id.
    """
    needle = query.lower()
    rows: list[dict] = []
    for doc in list_documents(corpus):
        title = doc.title or ""
        idx = title.lower().find(needle)
        if idx < 0:
            continue
        rows.append({
            "doc_id": doc.id,
            "title": title,
            "match_offset": idx,
            "title_len": len(title),
        })
    rows.sort(key=lambda r: (r["match_offset"], r["title_len"], r["doc_id"]))
    return rows[:top_k] if top_k is not None else rows


def search_papers(
    corpus: Corpus,
    query: str,
    *,
    top_k: int = 8,
    chunk_pool: int | None = None,
    text: bool = False,
    rank: str = "semantic",
) -> list[dict]:
    """Search aggregated to the paper level: best chunk per doc.

    Returns a sorted list of ``{doc_id, title, best_score, n_chunks,
    best_chunk_id, chunk_ids}`` records. ``text=True`` switches the
    underlying chunk match from semantic to literal substring grep.
    """
    pool = chunk_pool or max(top_k * 5, top_k)
    if text:
        hits = search_text(corpus, query, top_k=pool)
    else:
        hits = search_chunks(corpus, query, top_k=pool, rank=rank)
    docs_by_id = {d.id: d for d in list_documents(corpus)}
    grouped: dict[str, dict] = {}
    for hit in hits:
        doc_id = str(hit.get("doc_id") or hit.get("source_id") or "")
        chunk_id = str(hit.get("id") or "")
        if not doc_id:
            continue
        score = float(hit.get("score", 0.0) or 0.0)
        entry = grouped.setdefault(
            doc_id,
            {
                "doc_id": doc_id,
                "title": docs_by_id[doc_id].title if doc_id in docs_by_id else "",
                "best_score": score,
                "n_chunks": 0,
                "best_chunk_id": chunk_id,
                "chunk_ids": [],
            },
        )
        entry["n_chunks"] += 1
        entry["chunk_ids"].append(chunk_id)
        if score > float(entry["best_score"]):
            entry["best_score"] = score
            entry["best_chunk_id"] = chunk_id
    return sorted(
        grouped.values(),
        key=lambda item: (
            -float(item["best_score"]),
            -int(item["n_chunks"]),
            str(item["doc_id"]),
        ),
    )[:top_k]


def rank_docs(
    corpus: Corpus,
    *,
    by: str,
    top_k: int = 8,
) -> list[dict]:
    """Return the top-``top_k`` documents ranked by a graph metric.

    ``by`` is one of ``citation_count`` or ``pagerank``. Each item has
    ``doc_id``, ``title``, ``citation_count``, ``pagerank``.
    """
    from .store.routing import is_sqlite

    if is_sqlite():
        rows = _rank_docs_sqlite(corpus)
    else:
        vs = read_vector_store(corpus)
        kg = read_knowledge_graph(corpus, vectors=vs)
        backend = kg._backend
        docs_by_id = {d.id: d for d in list_documents(corpus)}
        rows = []
        for doc_id, doc in docs_by_id.items():
            if doc_id not in backend.G:
                continue
            attrs = backend.G.nodes[doc_id]
            rows.append(
                {
                    "doc_id": doc_id,
                    "title": doc.title or "",
                    "citation_count": int(attrs.get("citation_count", 0) or 0),
                    "pagerank": float(attrs.get("pagerank", 0.0) or 0.0),
                }
            )
    if by == "citation_count":
        rows.sort(key=lambda r: (-r["citation_count"], -r["pagerank"], r["doc_id"]))
    elif by == "pagerank":
        rows.sort(key=lambda r: (-r["pagerank"], -r["citation_count"], r["doc_id"]))
    else:
        raise ValueError(f"unknown rank metric: {by!r}; expected citation_count or pagerank")
    return rows[:top_k]


def _rank_docs_sqlite(corpus: Corpus) -> list[dict]:
    """Pull citation_count + pagerank from node_metrics."""
    from .store.metrics import (
        CITATION_COUNT,
        VIEW_CORPUS_CITATION,
    )
    from .store.routing import open_store

    store = open_store(corpus.root)
    try:
        cite_rows = dict(store.con.execute(
            "SELECT node_id, value FROM node_metrics "
            "WHERE graph_name=? AND node_type='document' AND metric=?",
            (VIEW_CORPUS_CITATION, CITATION_COUNT),
        ))
        pr_rows = dict(store.con.execute(
            "SELECT node_id, value FROM node_metrics "
            "WHERE graph_name=? AND node_type='document' AND metric='pagerank'",
            (VIEW_CORPUS_CITATION,),
        ))
        out: list[dict] = []
        for d in store.list_documents():
            out.append({
                "doc_id": d["doc_id"],
                "title": d["title"] or "",
                "citation_count": int(cite_rows.get(d["doc_id"], 0)),
                "pagerank": float(pr_rows.get(d["doc_id"], 0.0)),
            })
        return out
    finally:
        store.close()


def doc_metrics(corpus: Corpus, doc_ids: list[str]) -> dict[str, dict]:
    """Return ``{doc_id: {citation_count, pagerank}}`` for the listed docs.

    Falls back to zeros when the corpus has no derived knowledge graph
    or vector store (typical of hand-built test fixtures).
    """
    if not doc_ids:
        return {}
    from .store.routing import is_sqlite
    if is_sqlite():
        rows = {r["doc_id"]: r for r in _rank_docs_sqlite(corpus)}
        return {
            did: {
                "citation_count": int(rows.get(did, {}).get("citation_count", 0)),
                "pagerank": float(rows.get(did, {}).get("pagerank", 0.0)),
            }
            for did in doc_ids
        }
    if not (corpus.knowledge_graph_path.exists() and corpus.vectors_path.exists()):
        return {did: {"citation_count": 0, "pagerank": 0.0} for did in doc_ids}
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    backend = kg._backend
    out: dict[str, dict] = {}
    for did in doc_ids:
        if did not in backend.G:
            out[did] = {"citation_count": 0, "pagerank": 0.0}
            continue
        attrs = backend.G.nodes[did]
        out[did] = {
            "citation_count": int(attrs.get("citation_count", 0) or 0),
            "pagerank": float(attrs.get("pagerank", 0.0) or 0.0),
        }
    return out


def search_text(corpus: Corpus, needle: str, *, top_k: int = 50) -> list[dict]:
    """Literal substring grep over chunk text. Cheap, no embedding load."""
    needle_lower = needle.lower()
    out: list[dict] = []
    for c in all_chunks(corpus):
        if needle_lower in c.text.lower():
            out.append({"id": c.id, "doc_id": c.doc_id, "preview": c.text[:160]})
            if len(out) >= top_k:
                break
    return out


def sample_docs(
    corpus: Corpus,
    *,
    max_docs: int,
    strategy: str = "diverse",
    pagerank_weight: float = 0.7,
) -> list[str]:
    """Sample doc ids from the corpus by strategy.

    ``strategy="diverse"`` runs greedy-submodular selection blending
    PageRank prior and coverage gain over mean-pooled doc embeddings.
    Future strategies (``"random"``, ``"pagerank"``,
    ``"stratified"``) will surface here without changing callers.

    Knobs are caller-supplied; the CLI surface
    (``corpus sample --max <n> --strategy <name>
    --pagerank-weight <w>``) carries the user-facing defaults so the
    value is explicit at the agent boundary.
    """
    if strategy != "diverse":
        raise ValueError(
            f"unknown sampling strategy {strategy!r}; only 'diverse' is "
            f"implemented today (future: 'random', 'pagerank')"
        )
    from .sampling import doc_embeddings, pagerank_normalised, sample_diverse

    chunks = all_chunks(corpus)
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    embeds, doc_order = doc_embeddings(chunks, vs)
    pr_norm = pagerank_normalised(kg, doc_order)
    return list(
        sample_diverse(
            doc_order=doc_order,
            doc_embeddings=embeds,
            pr_norm=pr_norm,
            max_docs=max_docs,
            pagerank_weight=pagerank_weight,
        )
    )


# ------------------------------------------------------------------- check


def check_corpus(corpus: Corpus, *, full: bool = False) -> dict:
    """Lightweight corpus health summary used by ``corpus check``.

    Reports doc/chunk counts, derived-artifact presence, and detected
    field. The default form does **not** load the knowledge graph
    (~18MB JSON for the ALD corpus, ~6s wall-clock) so the call stays
    fast. Pass ``full=True`` to also report citation-marker indexing
    coverage (``traverse <chunk> --to cited-in-corpus`` requires
    sources with populated ``ord_refs``); this triggers a full KG
    parse via ``json.load`` + a streaming pass over the ``nodes``
    array.
    """
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    out: dict = {
        "root": str(corpus.root),
        "n_docs": len(docs),
        "n_chunks": len(chunks),
        "has_vectors": corpus.vectors_path.exists(),
        "has_knowledge_graph": corpus.knowledge_graph_path.exists(),
        "has_manifest": corpus.manifest_path.exists(),
    }
    try:
        from .field_detect import detect_field, detect_field_scores

        out["field"] = detect_field(corpus)
        out["field_scores"] = detect_field_scores(corpus)[:5]
    except Exception as exc:
        out["field"] = None
        out["field_error"] = str(exc)
    if full and out["has_knowledge_graph"]:
        try:
            out.update(_ord_refs_coverage(corpus, docs))
        except Exception as exc:
            out["ord_refs_coverage_pct"] = None
            out["ord_refs_error"] = str(exc)
    return out


def _ord_refs_coverage(corpus: Corpus, docs: list[Document]) -> dict:
    """Count in-corpus source nodes with a populated ``ord_refs`` attribute.

    Reads ``knowledge_graph.json`` directly via ``json.load`` and
    iterates the ``nodes`` array — avoids the full NetworkX backend
    construction that the regular KG load performs. Roughly 6x faster
    than a full ``read_knowledge_graph`` on the ALD reference corpus.
    """
    import json as _json

    with corpus.knowledge_graph_path.open(encoding="utf-8") as fh:
        kg_blob = _json.load(fh)
    in_corpus_ids = {d.id for d in docs}
    with_ord = 0
    for node in kg_blob.get("nodes", []):
        if node.get("type") != "source":
            continue
        if node.get("id") not in in_corpus_ids:
            continue
        ord_refs = node.get("ord_refs") or {}
        if ord_refs:
            with_ord += 1
    n = len(in_corpus_ids)
    return {
        "sources_with_ord_refs": with_ord,
        "ord_refs_coverage_pct": (
            round(100.0 * with_ord / n, 1) if n else 0.0
        ),
    }


# ---------------------------------------------------------------- traverse


def traverse_doc(
    corpus: Corpus,
    *,
    doc_id: str,
    relation: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from a doc handle.

    Relations:

    - ``cited-by``      sources that cite this source
    - ``references``    sources cited by this source
    - ``chunks``        chunks belonging to this source
    - ``figures``       figures belonging to this source
    - ``equations``     equations belonging to this source

    For source-typed results, ``rank`` may be ``citation_count`` or
    ``pagerank``; ``top_k`` limits the result.
    """
    if relation == "chunks":
        # Bypass the graph backend so the output order is deterministic
        # (document order by ``ord``) and rows carry section_path + ord
        # — both needed by the agent to filter without an N+1 round-trip.
        # Reading from chunks/<doc>.jsonl also avoids loading the
        # vector store / KG when the agent only wants paper structure.
        chunks = sorted(
            (c for c in read_chunks(corpus, doc_id) if not c.is_boilerplate),
            key=lambda c: c.ord,
        )
        rows = [
            {
                "id": c.id,
                "type": "chunk",
                "doc_id": c.doc_id,
                "ord": c.ord,
                "section_path": list(c.section_path or []),
            }
            for c in chunks
        ]
        if top_k is not None:
            rows = rows[:top_k]
        return rows
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    backend = kg._backend
    if doc_id not in backend.G:
        return []
    qb = kg.source(doc_id)
    if relation == "cited-by":
        result = qb.cited_by()
    elif relation == "references":
        result = qb.references()
    elif relation == "figures":
        result = qb.figures()
    elif relation == "equations":
        result = qb.equations()
    elif relation == "authors":
        result = qb.authors()
    else:
        raise ValueError(
            f"unknown doc relation {relation!r}; expected "
            f"cited-by | references | chunks | figures | equations | authors"
        )
    return _materialize_traversal(
        backend, result.ids(), rank=rank, top_k=top_k, corpus=corpus
    )


def traverse_author(
    corpus: Corpus,
    *,
    key: str,
    relation: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from an author key.

    Relations:

    - ``sources``    papers by this author
    - ``coauthors``  authors who share a paper with this author
    """
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    backend = kg._backend
    if key not in backend.G:
        return []
    qb = kg.author(key)
    if relation == "sources":
        result = qb.sources()
    elif relation == "coauthors":
        result = qb.coauthors()
    else:
        raise ValueError(
            f"unknown author relation {relation!r}; expected sources | coauthors"
        )
    return _materialize_traversal(
        backend, result.ids(), rank=rank, top_k=top_k, corpus=corpus
    )


def traverse_chunk(
    corpus: Corpus,
    *,
    chunk_id: str,
    relation: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from a chunk handle.

    Relations:

    - ``source``            the doc this chunk belongs to
    - ``cited-in-corpus``   in-corpus sources cited by markers in this chunk's text
    - ``figures``           figures discussed near this chunk (FIGURE_NEAR_CHUNK)
    - ``equations``         equations contained in this chunk
    """
    chunk = get_chunk(corpus, chunk_id)
    if chunk is None:
        return []
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    backend = kg._backend
    if relation == "source":
        return _materialize_traversal(
            backend, [chunk.doc_id], rank=rank, top_k=top_k, corpus=corpus
        )
    if relation == "cited-in-corpus":
        import sys

        from .graph import parse_citation_markers

        ords = parse_citation_markers(chunk.text)
        if not ords:
            return []
        if chunk.doc_id not in backend.G:
            return []
        result = kg.source(chunk.doc_id).references(ords=ords)
        rows = _materialize_traversal(
            backend, result.ids(), rank=rank, top_k=top_k, corpus=corpus
        )
        # Silent-zero is hostile: agents cannot tell whether the chunk
        # has no markers or every marker was out-of-corpus. When
        # markers exist but resolved zero, hint on stderr (suppressible
        # via WIKIFY_QUIET=1).
        if not rows:
            import os

            if os.environ.get("WIKIFY_QUIET") != "1":
                preview = ",".join(str(o) for o in ords[:8])
                more = f" (+{len(ords) - 8})" if len(ords) > 8 else ""
                print(
                    f"hint: chunk has {len(ords)} citation marker(s) "
                    f"[{preview}{more}] but none resolved to in-corpus refs; "
                    f"references may be out-of-corpus or unindexed",
                    file=sys.stderr,
                )
        return rows
    if relation in {"figures", "equations"}:
        # Build a chunk-scoped QueryBuilder directly (no public entry point
        # for a single chunk, but the constructor is the documented internal
        # contract).
        from .graph import CHUNK, QueryBuilder

        if chunk.id not in backend.G:
            return []
        qb = QueryBuilder(kg, {chunk.id}, CHUNK)
        result = qb.nearby_figures() if relation == "figures" else qb.nearby_equations()
        return _materialize_traversal(
            backend, result.ids(), rank=rank, top_k=top_k, corpus=corpus
        )
    raise ValueError(
        f"unknown chunk relation {relation!r}; expected "
        f"source | cited-in-corpus | figures | equations"
    )


def _materialize_traversal(
    backend,
    ids: list[str],
    *,
    rank: str | None,
    top_k: int | None,
    corpus: Corpus | None = None,
) -> list[dict]:
    """Pivot a list of node ids into rich rows, optionally ranked + truncated."""
    rows: list[dict] = []
    for nid in ids:
        if nid not in backend.G:
            continue
        attrs = backend.G.nodes[nid]
        ntype = attrs.get("type", "")
        if ntype == "chunk":
            rows.append({
                "id": nid,
                "type": "chunk",
                "doc_id": attrs.get("source_id", ""),
            })
        elif ntype == "figure":
            raw_path = str(attrs.get("path", "") or "")
            path = (
                _normalize_corpus_path(corpus, raw_path)
                if corpus is not None else raw_path.replace("\\", "/")
            )
            rows.append({
                "id": nid,
                "type": "figure",
                "doc_id": attrs.get("source_id", ""),
                "page": attrs.get("page"),
                "caption": attrs.get("caption", "") or "",
                "path": path,
            })
        elif ntype == "equation":
            rows.append({
                "id": nid,
                "type": "equation",
                "doc_id": attrs.get("source_id", ""),
                "latex": attrs.get("latex", "") or "",
                "label": attrs.get("label", "") or "",
                "kind": attrs.get("kind", "") or "",
                "is_chemical": bool(attrs.get("is_chemical", False)),
            })
        elif ntype == "author":
            n_papers = len(backend._sources_of.get(nid, set()))
            rows.append({
                "id": nid,
                "type": "author",
                "name": attrs.get("display_name", "") or attrs.get("name", "") or "",
                "h_index": int(attrs.get("h_index", 0) or 0),
                "citation_count": int(attrs.get("citation_count", 0) or 0),
                "n_papers": n_papers,
            })
        else:
            rows.append({
                "id": nid,
                "type": ntype or "source",
                "title": attrs.get("title", ""),
                "citation_count": int(attrs.get("citation_count", 0) or 0),
                "pagerank": float(attrs.get("pagerank", 0.0) or 0.0),
            })
    if rank == "citation_count":
        rows.sort(
            key=lambda r: (
                -int(r.get("citation_count", 0)),
                -float(r.get("pagerank", 0.0)),
                str(r.get("id", "")),
            )
        )
    elif rank == "pagerank":
        rows.sort(
            key=lambda r: (
                -float(r.get("pagerank", 0.0)),
                -int(r.get("citation_count", 0)),
                str(r.get("id", "")),
            )
        )
    elif rank == "h_index":
        rows.sort(
            key=lambda r: (
                -int(r.get("h_index", 0)),
                -int(r.get("citation_count", 0)),
                str(r.get("id", "")),
            )
        )
    elif rank == "n_papers":
        rows.sort(
            key=lambda r: (
                -int(r.get("n_papers", 0)),
                -int(r.get("h_index", 0)),
                str(r.get("id", "")),
            )
        )
    elif rank is not None:
        raise ValueError(
            f"unknown rank metric {rank!r}; expected "
            f"citation_count | pagerank | h_index | n_papers"
        )
    if top_k is not None:
        rows = rows[:top_k]
    return rows


# --------------------------------------------------------- evidence helper


# Strip leading section numbering ("3.", "3.2", "I.", "IV.", "A.") so
# that ``sections=["introduction"]`` matches a chunk whose
# ``section_path`` is ``["I. INTRODUCTION"]``. Anchored at the start.
_LEADING_NUMERIC_RE = re.compile(
    r"^\s*(?:[ivxlcdm]+\.|[a-z]\.|\d+(?:\.\d+)*\.?)\s*",
    re.IGNORECASE,
)


def _normalize_section_token(s: str) -> str:
    """Lowercase, strip leading numbering, collapse whitespace.

    ``"I. INTRODUCTION"`` -> ``"introduction"``. ``"3.2 Photoactivity"``
    -> ``"photoactivity"``. ``"Summary"`` -> ``"summary"``.
    """
    out = (s or "").lower().strip()
    out = _LEADING_NUMERIC_RE.sub("", out)
    return out.strip()


def _section_matches(path: list[str], wanted: list[str]) -> bool:
    """True if any element of *path* matches any wanted token.

    Match is bidirectional substring after normalisation, so
    ``wanted=['summary']`` hits ``["V. SUMMARY"]`` and
    ``wanted=['intro']`` hits ``["1. Introduction"]``. Also matches
    against the joined ``"a > b > c"`` form so deep paths work.
    """
    if not path:
        return False
    norm_parts = [_normalize_section_token(p) for p in path]
    norm_joined = " > ".join(norm_parts)
    norm_wanted = [_normalize_section_token(w) for w in wanted if w]
    for w in norm_wanted:
        if not w:
            continue
        if w in norm_joined or norm_joined.startswith(w):
            return True
        for p in norm_parts:
            if w in p or p.startswith(w) or w.startswith(p) and p:
                return True
    return False


def _is_caption_chunk(chunk) -> bool:
    """Figure-caption chunks are tagged ``section_path[0] == '__image__'``."""
    path = chunk.section_path or []
    return bool(path) and path[0] == "__image__"


def _doc_body_chunks(corpus: Corpus, doc_id: str) -> list:
    """Body chunks of one doc — boilerplate, captions, references stripped.

    Mirrors the filters the rest of the pipeline already applies (see
    ``ingest.config.SKIP_SECTION_TYPES`` and ``abstract_tagger`` for
    the canonical list). Centralising the filter here keeps the agent
    surface in lockstep with what the writer pipeline considers prose.
    """
    from ..ingest.config import SKIP_SECTION_TYPES

    return sorted(
        (
            c for c in read_chunks(corpus, doc_id)
            if not c.is_boilerplate
            and c.section_type not in SKIP_SECTION_TYPES
            and not _is_caption_chunk(c)
        ),
        key=lambda c: c.ord,
    )


def read_doc_text(
    corpus: Corpus,
    doc_id: str,
    *,
    sections: list[str] | None = None,
) -> dict:
    """Return the body of one document as ordered text segments.

    Reads chunks in document order (by ``ord``), groups consecutive
    chunks that share the same ``section_path`` into one segment, and
    optionally filters by section name (case-insensitive, leading
    numbering tolerated, substring match against any path element).

    Returns ``{"segments": [...], "available_section_paths": [...],
    "matched_section_paths": [...]}``. Each segment carries
    ``section_path``, ``text``, ``chunk_ids`` (in order), and
    ``ord_range``. Figure captions (``__image__`` section), boilerplate,
    references, acknowledgments, and appendices are excluded so the
    body reads as prose; figure captions live in ``corpus_traverse
    doc -> figures`` and ``corpus_show figure:...``.

    ``available_section_paths`` lists every section the doc has (after
    filtering); ``matched_section_paths`` lists which the caller's
    ``sections`` filter actually hit. An empty filter result returns
    ``segments=[]`` with both lists populated so the caller can tell
    "wrong key" from "no content".
    """
    body = _doc_body_chunks(corpus, doc_id)
    available = _ordered_unique_paths(body)

    if sections:
        kept = [c for c in body if _section_matches(list(c.section_path or []), sections)]
    else:
        kept = body
    matched = _ordered_unique_paths(kept)

    segments: list[dict] = []
    for c in kept:
        path = list(c.section_path or [])
        if segments and segments[-1]["section_path"] == path:
            tail = segments[-1]
            tail["text"] = (tail["text"] + "\n\n" + c.text).strip()
            tail["chunk_ids"].append(c.id)
            tail["ord_range"][1] = c.ord
        else:
            segments.append({
                "section_path": path,
                "text": c.text,
                "chunk_ids": [c.id],
                "ord_range": [c.ord, c.ord],
            })
    return {
        "segments": segments,
        "available_section_paths": available,
        "matched_section_paths": matched,
    }


def _ordered_unique_paths(chunks: list) -> list[list[str]]:
    seen: list[list[str]] = []
    seen_set: set[tuple[str, ...]] = set()
    for c in chunks:
        path = tuple(c.section_path or [])
        if path not in seen_set:
            seen_set.add(path)
            seen.append(list(path))
    return seen


def doc_section_index(corpus: Corpus, doc_id: str) -> list[dict]:
    """Return ``[{section_path, n_chunks, ord_range}]`` for one doc.

    Cheap structural overview the agent can read before deciding
    whether to fetch full text. Same filter as :func:`read_doc_text`:
    drops boilerplate, figure-caption (``__image__``), references,
    acknowledgments, and appendix chunks.
    """
    chunks = _doc_body_chunks(corpus, doc_id)
    out: list[dict] = []
    for c in chunks:
        path = list(c.section_path or [])
        if out and out[-1]["section_path"] == path:
            tail = out[-1]
            tail["n_chunks"] += 1
            tail["ord_range"][1] = c.ord
        else:
            out.append({
                "section_path": path,
                "n_chunks": 1,
                "ord_range": [c.ord, c.ord],
            })
    return out


def chunk_section(corpus: Corpus, chunk_id: str) -> list[str] | None:
    """Return the ``section_path`` of one chunk, or ``None`` if missing.

    Used to enrich paper-search rows with ``best_chunk_section`` so the
    agent can tell whether a hit came from the abstract vs. references
    without an extra round-trip.
    """
    chunk = get_chunk(corpus, chunk_id)
    if chunk is None:
        return None
    return list(chunk.section_path or [])


def select_evidence_chunks(
    corpus: Corpus,
    *,
    page_title: str,
    top_k: int = 8,
    max_per_source: int = 2,
) -> list[str]:
    """Per-page evidence helper. Returns a list of chunk ids."""
    vs = read_vector_store(corpus)
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for

    meta = read_meta(corpus.vectors_path)
    embed = (
        embedder_for(meta.backend, meta.model, mode="query") if meta else None
    )
    kg = read_knowledge_graph(corpus, vectors=vs, embed_fn=embed)
    seen_per_source: dict[str, int] = {}
    out: list[str] = []
    hits = kg.chunks().search(page_title, top_k=top_k * 4)
    for h in hits:
        src = h.get("source_id") or h.get("doc_id") or ""
        if seen_per_source.get(src, 0) >= max_per_source:
            continue
        out.append(h["id"])
        seen_per_source[src] = seen_per_source.get(src, 0) + 1
        if len(out) >= top_k:
            break
    return out


# ----------------------------------------------------- orchestrators (CLI + MCP)


class QueryError(ValueError):
    """Validation error from :func:`find` / :func:`traverse` / :func:`show`.

    Carries a stable ``code`` so adapters can surface a structured error
    (CLI: ``cli_error(code=...)``; MCP: ``{"ok": False, "code": ...}``).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_DOC_RELATIONS = {
    "cited-by", "references", "chunks", "figures", "equations", "authors",
}
_CHUNK_RELATIONS = {"source", "cited-in-corpus", "figures", "equations"}
_AUTHOR_RELATIONS = {"sources", "coauthors"}

_SOURCE_RANKS = {"citation_count", "pagerank"}
_AUTHOR_RANKS = {"h_index", "citation_count", "n_papers"}
_LEXICAL_RANKS = {"bm25", "hybrid"}
_FIND_RANKS = {"semantic", *_LEXICAL_RANKS, *_SOURCE_RANKS, *_AUTHOR_RANKS}


SCHEMA: dict = {
    "node_types": {
        "source": "A document. Handle: doc:<id-or-short>.",
        "chunk": "A text chunk inside a doc. Handle: chunk:<id-or-short>.",
        "author": "A paper author. Handle: author:<lastname-initials key>.",
        "section": "A section of a doc.",
        "figure": "An image with caption. Handle: figure:<doc-short>/<stem>.",
        "equation": "A math or chemical equation. Handle: equation:<id>.",
    },
    "edge_kinds": [
        "CITES",
        "AUTHORED_BY",
        "COLLABORATED",
        "CONTAINS_SECTION",
        "CONTAINS_CHUNK",
        "CHUNK_IN_SECTION",
        "CONTAINS_FIGURE",
        "CONTAINS_EQUATION",
        "FIGURE_NEAR_CHUNK",
        "EQUATION_IN_CHUNK",
    ],
    "traverse_relations": {
        "doc": sorted(_DOC_RELATIONS),
        "chunk": sorted(_CHUNK_RELATIONS),
        "author": sorted(_AUTHOR_RELATIONS),
    },
    "rank_metrics": {
        "source": sorted(_SOURCE_RANKS),
        "author": sorted(_AUTHOR_RANKS),
    },
    "find_modes": {
        "--by chunk":  "Rank chunks (default).",
        "--by paper":  "Aggregate chunk hits to papers.",
        "--by author": "Aggregate chunk hits to authors.",
        "--text":      "Literal substring grep over chunk text.",
        "--field title": (
            "Literal substring search over Document.title. Use with "
            "--by paper for 'paper whose title mentions X'."
        ),
    },
    "sample_strategies": {
        "diverse": (
            "Greedy submodular: PageRank prior + coverage gain over doc "
            "embeddings."
        ),
    },
    "formats": ["auto", "quiet", "compact", "json"],
    "handle_resolution": (
        "Doc/equation handles use the trailing hex hash from the id "
        "(8+ chars). Figures use <doc-short>/<stem>. Author handles use "
        "the lowercase 'first_last' key with case-insensitive unique "
        "prefix. Chunk handles are content-derived (chunk text hashes), "
        "so identical chunk text across docs (figure captions, "
        "boilerplate) collides. When a short chunk handle is ambiguous, "
        "either pass the full id or pair it with a doc handle "
        "(e.g. via 'corpus_traverse doc:<short> --to chunks' to list "
        "chunks of one doc and pick by ord/section_path)."
    ),
}


_FIND_FIELDS = {"chunk_text", "title"}


def _validate_find_params(*, query: str, by: str, rank: str, top_k: int,
                          field: str) -> None:
    if top_k <= 0:
        raise QueryError("bad_top_k", f"top_k must be > 0; got {top_k}")
    if field not in _FIND_FIELDS:
        raise QueryError(
            "bad_field",
            f"unknown field {field!r}; expected "
            f"{' | '.join(sorted(_FIND_FIELDS))}",
        )
    if rank not in _FIND_RANKS:
        raise QueryError(
            "bad_rank",
            f"unknown rank {rank!r}; expected "
            f"{' | '.join(sorted(_FIND_RANKS))}",
        )
    if by not in {"chunk", "paper", "author"}:
        raise QueryError(
            "bad_by", f"unknown by {by!r}; expected chunk | paper | author"
        )
    if field == "title" and by != "paper":
        raise QueryError(
            "bad_field_by_combo",
            f"field='title' only applies with by='paper'; got by={by!r}",
        )
    if by == "chunk" and rank not in {"semantic", *_LEXICAL_RANKS}:
        raise QueryError(
            "bad_rank_by_combo",
            f"rank {rank!r} only applies when chunks are aggregated to a "
            f"parent doc/author. Use by='paper' or by='author', or drop "
            f"rank to keep semantic order.",
        )
    if by == "author" and rank == "pagerank":
        raise QueryError(
            "bad_rank_by_combo",
            "rank 'pagerank' does not apply to authors; use h_index | "
            "citation_count | n_papers.",
        )
    if by == "paper" and rank in {"h_index", "n_papers"}:
        raise QueryError(
            "bad_rank_by_combo",
            f"rank {rank!r} does not apply to papers; use citation_count "
            f"| pagerank, or switch to by='author'.",
        )


def _attach_best_chunk_section(corpus: Corpus, papers: list[dict]) -> list[dict]:
    """Add ``best_chunk_section`` to each paper row, when ``best_chunk_id`` is set.

    Lets the agent tell whether the hit came from the abstract or the
    references without an extra ``corpus_show chunk:<id>`` round-trip.
    """
    by_doc: dict[str, dict] = {}
    for p in papers:
        cid = str(p.get("best_chunk_id", "") or "")
        did = str(p.get("doc_id", "") or "")
        if not cid or not did:
            continue
        cache = by_doc.setdefault(did, {})
        if "_chunks" not in cache:
            cache["_chunks"] = {c.id: c for c in read_chunks(corpus, did)}
        chunk = cache["_chunks"].get(cid)
        if chunk is not None:
            p["best_chunk_section"] = list(chunk.section_path or [])
    return papers


def _rerank_papers(
    corpus: Corpus,
    papers: list[dict],
    *,
    rank: str,
    top_k: int,
) -> list[dict]:
    """Attach citation_count + pagerank, optionally re-sort, truncate to top_k."""
    doc_ids = [str(p.get("doc_id", "")) for p in papers]
    metrics = doc_metrics(corpus, doc_ids)
    enriched: list[dict] = []
    for p in papers:
        did = str(p.get("doc_id", ""))
        m = metrics.get(did, {})
        enriched.append(
            {
                **p,
                "citation_count": m.get("citation_count", 0),
                "pagerank": m.get("pagerank", 0.0),
            }
        )
    if rank == "citation_count":
        enriched.sort(
            key=lambda r: (
                -int(r.get("citation_count", 0)),
                -float(r.get("best_score", 0.0)),
                str(r.get("doc_id", "")),
            )
        )
    elif rank == "pagerank":
        enriched.sort(
            key=lambda r: (
                -float(r.get("pagerank", 0.0)),
                -float(r.get("best_score", 0.0)),
                str(r.get("doc_id", "")),
            )
        )
    enriched = enriched[:top_k]
    return _attach_best_chunk_section(corpus, enriched)


def find(
    corpus: Corpus,
    *,
    query: str,
    by: str = "chunk",
    rank: str = "semantic",
    top_k: int = 8,
    text: bool = False,
    field: str = "chunk_text",
) -> dict:
    """Validate + dispatch ``find``. Returns ``{kind, rows, scored}``.

    ``kind`` is one of:

    - ``"chunks"``     — chunk rows ``{id, doc_id, score?, preview?}``.
    - ``"papers"``     — paper rows with ``citation_count``/``pagerank``
      attached and re-ranked when ``rank`` is a graph metric.
    - ``"authors"``    — author rows from rank/search.
    - ``"docs"``       — pure metric ranking with no query.

    ``scored`` is True when rows carry a query score (``"score"`` for
    chunks, ``"best_score"`` for papers/authors).

    ``field`` selects what to search:

    - ``"chunk_text"`` (default): search chunk text and aggregate per
      ``by``.
    - ``"title"``: literal substring search over ``Document.title``.
      Only valid with ``by="paper"`` and a non-empty query.
    """
    _validate_find_params(
        query=query, by=by, rank=rank, top_k=top_k, field=field,
    )

    if field == "title":
        if not query:
            raise QueryError(
                "missing_query",
                "find with field='title' requires a non-empty query.",
            )
        rows = search_papers_by_title(
            corpus,
            query,
            top_k=(top_k if rank == "semantic" else None),
        )
        if rank in _SOURCE_RANKS:
            rows = _rerank_papers(corpus, rows, rank=rank, top_k=top_k)
        return {
            "kind": "papers",
            "rows": rows,
            "scored": False,
        }

    # Pure metric ranking — ignore query, return top docs by graph metric.
    if rank in _SOURCE_RANKS and not query and by != "author":
        return {
            "kind": "docs",
            "rows": rank_docs(corpus, by=rank, top_k=top_k),
            "scored": False,
        }

    # Author-only modes: top authors by metric, or authors by query.
    if by == "author":
        if not query:
            metric = rank if rank in _AUTHOR_RANKS else "h_index"
            return {
                "kind": "authors",
                "rows": rank_authors(corpus, by=metric, top_k=top_k),
                "scored": False,
            }
        return {
            "kind": "authors",
            "rows": search_authors(corpus, query, top_k=top_k),
            "scored": True,
        }

    # Widen the candidate pool when re-ranking by graph metric so the
    # most-cited paper that mentions the query isn't dropped at the
    # semantic top-K boundary.
    paper_pool = top_k if rank == "semantic" else max(top_k * 5, 30)

    if text:
        if by == "paper":
            papers = search_papers(corpus, query, top_k=paper_pool, text=True)
            return {
                "kind": "papers",
                "rows": _rerank_papers(corpus, papers, rank=rank, top_k=top_k),
                "scored": True,
            }
        return {
            "kind": "chunks",
            "rows": search_text(corpus, query, top_k=top_k),
            "scored": False,
        }

    if not query:
        raise QueryError(
            "missing_query",
            "find requires a query (or text=True). For query-less sampling, "
            "call sample_docs.",
        )

    if by == "paper":
        if rank in _LEXICAL_RANKS:
            papers = search_papers(corpus, query, top_k=top_k, rank=rank)
            return {"kind": "papers", "rows": papers, "scored": True}
        papers = search_papers(corpus, query, top_k=paper_pool)
        return {
            "kind": "papers",
            "rows": _rerank_papers(corpus, papers, rank=rank, top_k=top_k),
            "scored": True,
        }

    return {
        "kind": "chunks",
        "rows": search_chunks(corpus, query, top_k=top_k, rank=rank),
        "scored": True,
    }


def traverse(
    corpus: Corpus,
    *,
    handle: str,
    to: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> dict:
    """Validate + dispatch ``traverse``. Returns ``{handle_kind, rows}``.

    ``handle_kind`` is the parsed handle's leading kind (``doc``,
    ``chunk``, ``author``). ``rows`` are the heterogeneous traversal
    rows from :func:`traverse_doc` / :func:`traverse_chunk` /
    :func:`traverse_author`.
    """
    if top_k is not None and top_k < 0:
        raise QueryError(
            "bad_top_k", f"top_k must be >= 0 (0 means unlimited); got {top_k}"
        )
    try:
        kind, ident = parse_handle(handle)
    except ValueError as exc:
        raise QueryError("bad_handle", str(exc)) from exc

    top_k_effective = top_k if (top_k is not None and top_k > 0) else None

    if kind == "doc":
        if to not in _DOC_RELATIONS:
            raise QueryError(
                "bad_relation",
                f"unknown doc relation {to!r}; expected "
                f"{' | '.join(sorted(_DOC_RELATIONS))}",
            )
        full_id = resolve_doc_id(corpus, ident)
        rows = traverse_doc(
            corpus, doc_id=full_id, relation=to,
            rank=rank or None, top_k=top_k_effective,
        )
        return {"handle_kind": "doc", "rows": rows}

    if kind == "author":
        if to not in _AUTHOR_RELATIONS:
            raise QueryError(
                "bad_relation",
                f"unknown author relation {to!r}; expected "
                f"{' | '.join(sorted(_AUTHOR_RELATIONS))}",
            )
        full_key = resolve_author_key(corpus, ident)
        rows = traverse_author(
            corpus, key=full_key, relation=to,
            rank=rank or None, top_k=top_k_effective,
        )
        return {"handle_kind": "author", "rows": rows}

    if kind == "chunk":
        if to not in _CHUNK_RELATIONS:
            raise QueryError(
                "bad_relation",
                f"unknown chunk relation {to!r}; expected "
                f"{' | '.join(sorted(_CHUNK_RELATIONS))}",
            )
        full_id = resolve_chunk_id(corpus, ident)
        rows = traverse_chunk(
            corpus, chunk_id=full_id, relation=to,
            rank=rank or None, top_k=top_k_effective,
        )
        return {"handle_kind": "chunk", "rows": rows}

    raise QueryError(
        "bad_handle_kind",
        f"unknown handle kind {kind!r}; use doc:<id> | chunk:<id> | author:<key>",
    )


def show(corpus: Corpus, *, handle: str, full: bool = False) -> dict:
    """Resolve a handle and return its content.

    Returns ``{"handle_kind": "...", "data": ...}`` where ``data`` is:

    - ``Document`` for ``doc:`` handles,
    - ``Chunk`` for ``chunk:`` handles (full text vs preview gated by
      ``full``),
    - ``dict`` for ``figure:`` / ``equation:`` / ``author:`` (existing
      ``get_*`` payload shapes).

    The ``full`` flag only affects chunk text trimming; doc/figure/etc.
    are always returned in their full primitive shape.
    """
    try:
        kind, ident = parse_handle(handle)
    except ValueError as exc:
        raise QueryError("bad_handle", str(exc)) from exc

    if kind == "doc":
        doc = get_doc(corpus, ident)
        if doc is None:
            raise QueryError("doc_not_found", f"doc not found: {ident}")
        return {"handle_kind": "doc", "data": doc}

    if kind == "chunk":
        chunk = get_chunk(corpus, ident)
        if chunk is None:
            raise QueryError("chunk_not_found", f"chunk not found: {ident}")
        return {"handle_kind": "chunk", "data": chunk, "full": bool(full)}

    if kind == "figure":
        fig = get_figure(corpus, ident)
        if fig is None:
            raise QueryError("figure_not_found", f"figure not found: {ident}")
        return {"handle_kind": "figure", "data": fig}

    if kind == "equation":
        eq = get_equation(corpus, ident)
        if eq is None:
            raise QueryError("equation_not_found", f"equation not found: {ident}")
        return {"handle_kind": "equation", "data": eq}

    if kind == "author":
        au = get_author(corpus, ident)
        if au is None:
            raise QueryError("author_not_found", f"author not found: {ident}")
        return {"handle_kind": "author", "data": au}

    raise QueryError(
        "bad_handle_kind",
        f"unknown handle kind {kind!r}; use doc:<id>, chunk:<id>, "
        f"figure:<id>, equation:<id>, or author:<key>",
    )
