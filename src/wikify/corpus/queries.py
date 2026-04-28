"""Read-only corpus query helpers — the surface ``cli/corpus.py`` calls.

Wraps the existing fluent KG (``corpus/graph.py``), the seed selector
(``corpus/seed.py``), and the on-disk corpus loaders (``corpus/chunks.py``)
into one cohesive module that the CLI can drive without sprinkling
imports across handlers.

Handle grammar (used by ``corpus show <handle>``)::

    doc:<doc_id>          full id
    doc:<short>           hash-suffix or unique suffix; resolved against the corpus
    chunk:<chunk_id>      same rules

See ``corpus/handles.py`` for the resolution semantics.
"""

from __future__ import annotations

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
    """Split a ``kind:id`` handle. Raise ``ValueError`` if malformed."""
    if ":" not in handle:
        raise ValueError(
            f"handle must be 'kind:id' (e.g. 'doc:5f92b0389ccd', "
            f"'chunk:499c6728', 'figure:5f92.../fig_002', "
            f"'author:sungjun_kim'); got {handle!r}"
        )
    kind, _, ident = handle.partition(":")
    return kind, ident


# ------------------------------------------------------------------- find


def search_chunks(corpus: Corpus, query: str, *, top_k: int = 8) -> list[dict]:
    """Semantic search over chunk embeddings; returns ranked chunk dicts.

    Each result has ``id``, ``score``, and ``doc_id`` (so the agent can
    follow up with ``corpus show chunk:<id>`` or ``corpus show
    doc:<doc_id>``).
    """
    vs = read_vector_store(corpus)
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for

    meta = read_meta(corpus.vectors_path)
    embed = (
        embedder_for(meta.backend, meta.model, mode="query") if meta else None
    )
    kg = read_knowledge_graph(corpus, vectors=vs, embed_fn=embed)
    return list(kg.chunks().search(query, top_k=top_k))


def search_papers(
    corpus: Corpus,
    query: str,
    *,
    top_k: int = 8,
    chunk_pool: int | None = None,
    text: bool = False,
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
        hits = search_chunks(corpus, query, top_k=pool)
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
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    backend = kg._backend
    docs_by_id = {d.id: d for d in list_documents(corpus)}
    rows: list[dict] = []
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


def doc_metrics(corpus: Corpus, doc_ids: list[str]) -> dict[str, dict]:
    """Return ``{doc_id: {citation_count, pagerank}}`` for the listed docs.

    Falls back to zeros when the corpus has no derived knowledge graph
    or vector store (typical of hand-built test fixtures).
    """
    if not doc_ids:
        return {}
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


def find_seeds(
    corpus: Corpus,
    *,
    max_seeds: int,
    pagerank_weight: float,
) -> list[str]:
    """Return the greedy-submodular seed document ids.

    Both knobs are caller-supplied; the CLI surface defines the
    user-facing defaults (``corpus find --seed --max <n>
    --pagerank-weight <w>``). No defaults live here so callers cannot
    silently rely on a hidden policy.
    """
    from .seed import doc_embeddings, greedy_seed_select, pagerank_normalised

    chunks = all_chunks(corpus)
    vs = read_vector_store(corpus)
    kg = read_knowledge_graph(corpus, vectors=vs)
    embeds, doc_order = doc_embeddings(chunks, vs)
    pr_norm = pagerank_normalised(kg, doc_order)
    return list(
        greedy_seed_select(
            doc_order=doc_order,
            doc_embeddings=embeds,
            pr_norm=pr_norm,
            max_seeds=max_seeds,
            pagerank_weight=pagerank_weight,
        )
    )


# ------------------------------------------------------------------- check


def check_corpus(corpus: Corpus) -> dict:
    """Lightweight corpus health summary used by ``corpus check``."""
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
    return out


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
    elif relation == "chunks":
        result = qb.chunks()
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
        from .graph import parse_citation_markers

        ords = parse_citation_markers(chunk.text)
        if not ords:
            return []
        if chunk.doc_id not in backend.G:
            return []
        result = kg.source(chunk.doc_id).references(ords=ords)
        return _materialize_traversal(
            backend, result.ids(), rank=rank, top_k=top_k, corpus=corpus
        )
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
