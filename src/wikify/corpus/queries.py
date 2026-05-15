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
from .handles import AmbiguousHandleError, HandleNotFoundError
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


def get_doc_markdown(corpus: Corpus, doc_id: str) -> str:
    """Return persisted per-document markdown text, or an empty string."""
    doc = get_doc(corpus, doc_id)
    if doc is None:
        return ""
    from pathlib import Path

    path = Path(doc.markdown_path)
    if not path.is_absolute():
        path = corpus.root / path
    try:
        path = path.resolve()
        path.relative_to(corpus.root.resolve())
    except (OSError, ValueError):
        path = corpus.markdown_dir / f"{doc.id}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def equations_for_chunks(
    corpus: Corpus, chunk_ids: list[str]
) -> dict[str, list[str]]:
    """Return ``{chunk_id: [equation_content, ...]}`` for the listed chunks.

    Pulls equation assets bound to each chunk via ``chunk_assets``.
    Empty list per chunk when the corpus has no equation assets for
    that chunk or no SQLite store is available.
    """
    if not chunk_ids:
        return {}
    from .store.routing import open_store, sqlite_available

    out: dict[str, list[str]] = {cid: [] for cid in chunk_ids}
    if not sqlite_available(corpus.root):
        return out
    store = open_store(corpus.root)
    try:
        placeholders = ",".join("?" * len(chunk_ids))
        rows = store.con.execute(
            f"SELECT ca.chunk_id, a.content "
            f"FROM chunk_assets ca JOIN assets a ON a.asset_id=ca.asset_id "
            f"WHERE a.asset_type='equation' "
            f"AND ca.chunk_id IN ({placeholders}) "
            f"AND length(coalesce(a.content,'')) > 0 "
            f"ORDER BY a.ord",
            chunk_ids,
        )
        for r in rows:
            cid = r["chunk_id"]
            content = (r["content"] or "").strip()
            if content and cid in out:
                out[cid].append(content)
    finally:
        store.close()
    return out


_TABLE_REF_RE = re.compile(r"\bTab(?:le)?\.?\s*(\d{1,3})\b", re.IGNORECASE)
_FIGURE_REF_RE = re.compile(
    r"\bFig(?:ure)?\.?\s*(\d{1,3})\b|\bScheme\s*(\d{1,3})\b", re.IGNORECASE
)


def referenced_artifacts_for_chunks(
    corpus: Corpus, chunks: list[Chunk]
) -> dict[str, dict[str, list[str]]]:
    """Detect "Table N" / "Figure N" mentions in each chunk's text and
    pull the matching captioned artifacts from the same document.

    Returns ``{chunk_id: {"tables": [...], "figures": [...]}}``.
    Tables surface caption + content (markdown rendering). Figures
    surface caption only (image bytes are not relevant to the writer).
    Empty when the chunk text mentions no artifact or the artifact is
    not in the corpus.
    """
    if not chunks:
        return {}
    from .store.routing import open_store, sqlite_available

    out: dict[str, dict[str, list[str]]] = {
        c.id: {"tables": [], "figures": []} for c in chunks
    }
    if not sqlite_available(corpus.root):
        return out
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)

    store = open_store(corpus.root)
    try:
        for doc_id, doc_chunks in by_doc.items():
            # Pull every captioned table + figure for this document once.
            doc_assets = list(
                store.con.execute(
                    "SELECT asset_type, caption, content "
                    "FROM assets WHERE doc_id=? "
                    "AND asset_type IN ('table','figure') "
                    "AND length(coalesce(caption,'')) > 0",
                    (doc_id,),
                )
            )
            tables_by_num: dict[str, dict] = {}
            figures_by_num: dict[str, dict] = {}
            for r in doc_assets:
                cap = (r["caption"] or "").strip()
                content = (r["content"] or "").strip()
                m = re.match(
                    r"\W*(Table|Fig(?:ure)?|Scheme)\.?\s*(\d{1,3})\b",
                    cap, re.IGNORECASE,
                )
                if not m:
                    continue
                num = m.group(2)
                bucket = (
                    tables_by_num
                    if r["asset_type"] == "table"
                    else figures_by_num
                )
                # Keep the first asset for each number — duplicates are
                # rare and the first wins is deterministic.
                bucket.setdefault(num, {"caption": cap, "content": content})

            for c in doc_chunks:
                text = c.text or ""
                tnums = {m.group(1) for m in _TABLE_REF_RE.finditer(text)}
                fnums: set[str] = set()
                for m in _FIGURE_REF_RE.finditer(text):
                    fnums.add(m.group(1) or m.group(2))
                for n in sorted(tnums):
                    a = tables_by_num.get(n)
                    if a is None:
                        continue
                    rendered = a["caption"]
                    if a["content"]:
                        rendered += "\n\n" + a["content"]
                    out[c.id]["tables"].append(rendered)
                for n in sorted(fnums):
                    a = figures_by_num.get(n)
                    if a is None:
                        continue
                    out[c.id]["figures"].append(a["caption"])
    finally:
        store.close()
    return out


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
    """Resolve a short or full chunk handle to the canonical full id.

    Tries the standard handle resolver first — that already covers bare
    ``<chunk-short>`` and figure-namespaced caption-chunk forms like
    ``<doc-short>/Figure_01__caption`` (whose `short_id` includes the
    slash). Falls back to the disambiguated
    ``<doc-short>/<chunk-short>`` form emitted by `_emit_chunk_rows`
    only when the standard resolver does not find a match — that
    preserves the legacy slash semantics for caption chunks.
    """
    chunks = list(all_chunks(corpus))
    try:
        return resolve_short(short, (c.id for c in chunks))
    except HandleNotFoundError:
        if "/" not in short:
            raise
        # Compound disambiguation form: <doc-short>/<chunk-short>. Scope
        # candidates to the doc, then resolve the chunk-short within.
        from .handles import short_id
        doc_short, _, chunk_short = short.partition("/")
        candidates = [
            c for c in chunks
            if c.doc_id == doc_short
            or short_id(c.doc_id) == doc_short
            or c.doc_id.endswith("_" + doc_short)
        ]
        if not candidates:
            raise
        return resolve_short(chunk_short, (c.id for c in candidates))


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
    in_doc: str | None = None,
    exclude_kinds: list[str] | None = None,
) -> list[dict]:
    """Chunk search via the SQLite store.

    Each result has ``id``, ``score``, and ``doc_id``. ``rank`` is one of
    ``semantic`` (cosine over chunk embeddings), ``bm25`` (FTS5), or
    ``hybrid`` (RRF over BM25 + vector).

    When *in_doc* is set, the search is scoped to a single document.
    BM25 / text get a cheap WHERE filter; vector search post-filters
    a wider top-k pool down to the requested doc.

    When *exclude_kinds* is set, hits whose ``section_type`` matches
    any of the listed kinds are dropped. Typical caller usage:
    ``exclude_kinds=["references", "acknowledgments"]`` to keep
    bibliography chunks and acknowledgments paragraphs out of
    content retrieval.
    """
    from .store.routing import sqlite_available

    if not sqlite_available(corpus.root):
        if rank in _LEXICAL_RANKS or rank == _MULTI_RANK:
            raise QueryError(
                "no_wikify_db",
                f"--rank {rank} requires wikify.db; rebuild with `corpus build`",
            )
        # No SQLite store yet (hand-built test fixture or pre-build);
        # fall back to the empty KG so callers get [] instead of an error.
        return []
    if rank == _MULTI_RANK:
        return _search_chunks_all_modes(
            corpus, query, top_k=top_k, in_doc=in_doc,
            exclude_kinds=exclude_kinds,
        )
    return _search_chunks_sqlite(
        corpus, query, top_k=top_k, rank=rank, in_doc=in_doc,
        exclude_kinds=exclude_kinds,
    )


def _search_chunks_sqlite(
    corpus: Corpus, query: str, *,
    top_k: int, rank: str,
    in_doc: str | None = None,
    exclude_kinds: list[str] | None = None,
) -> list[dict]:
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for
    from .store.routing import active_space_id, open_store

    store = open_store(corpus.root)
    try:
        # Widen the candidate pool when post-filtering so the final
        # result still hits ``top_k`` after kind/in_doc filters drop hits.
        needs_wider = bool(in_doc) or bool(exclude_kinds)
        effective_top_k = max(top_k * 10, 50) if needs_wider else top_k

        if rank == "bm25":
            hits = store.search_chunks_bm25(
                query, top_k=effective_top_k, doc_id=in_doc,
            )
        elif rank == "hybrid":
            meta = read_meta(corpus.sqlite_path)
            embed = (
                embedder_for(meta.backend, meta.model, mode="query")
                if meta else None
            )
            qv = embed([query])[0] if embed else None  # type: ignore[index]
            space_id = active_space_id(store)
            hits = store.search_hybrid(
                query, query_vec=qv, space_id=space_id,
                top_k=effective_top_k,
            )
            if in_doc is not None:
                hits = _filter_hits_to_doc(store, hits, in_doc)
        else:
            # default semantic: cosine over the active embedding space.
            meta = read_meta(corpus.sqlite_path)
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
            hits = store.vector_index(space_id).search(qv, top_k=effective_top_k)
            if in_doc is not None:
                hits = _filter_hits_to_doc(store, hits, in_doc)
        if exclude_kinds:
            hits = _filter_hits_excluding_kinds(store, hits, exclude_kinds)
        hits = hits[:top_k]
        out: list[dict] = []
        for cid, score in hits:
            row = store.get_chunk(cid)
            if not row:
                continue
            out.append({
                "id": cid,
                "doc_id": row["doc_id"],
                "score": float(score),
                "section_type": row["section_type"] or "body",
                "is_boilerplate": bool(row["is_boilerplate"]),
            })
        return out
    finally:
        store.close()


def _filter_hits_to_doc(store, hits, doc_id: str) -> list[tuple[str, float]]:
    """Drop hits whose chunk does not belong to *doc_id*. One SQL round-trip."""
    if not hits:
        return []
    cids = [cid for cid, _ in hits]
    placeholders = ",".join("?" * len(cids))
    in_doc_set = {
        r[0] for r in store.con.execute(
            f"SELECT chunk_id FROM chunks WHERE doc_id = ? AND chunk_id IN ({placeholders})",
            [doc_id, *cids],
        )
    }
    return [(cid, score) for cid, score in hits if cid in in_doc_set]


def _filter_hits_excluding_kinds(
    store, hits, exclude_kinds: list[str],
) -> list[tuple[str, float]]:
    """Drop hits whose ``section_type`` matches any excluded kind.

    One SQL round-trip; agents pass ``exclude_kinds=["references",
    "acknowledgments"]`` to keep bibliography and acknowledgments
    paragraphs out of content retrieval. Empty ``exclude_kinds``
    returns the input unchanged.
    """
    if not hits or not exclude_kinds:
        return list(hits)
    cids = [cid for cid, _ in hits]
    placeholders = ",".join("?" * len(cids))
    rows = store.con.execute(
        f"SELECT chunk_id, section_type FROM chunks "
        f"WHERE chunk_id IN ({placeholders})",
        cids,
    ).fetchall()
    excluded = {str(k).lower() for k in exclude_kinds}
    keep = {
        r[0] for r in rows
        if (r[1] or "").lower() not in excluded
    }
    return [(cid, score) for cid, score in hits if cid in keep]


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
    exclude_kinds: list[str] | None = None,
) -> list[dict]:
    """Search aggregated to the paper level: best chunk per doc.

    Returns a sorted list of ``{doc_id, title, best_score, n_chunks,
    best_chunk_id, chunk_ids}`` records. ``text=True`` switches the
    underlying chunk match from semantic to literal substring grep.
    ``exclude_kinds`` drops chunks of those section types before
    aggregation, so a paper that only has matches in references /
    acknowledgments won't surface as a content hit.
    """
    pool = chunk_pool or max(top_k * 5, top_k)
    if text:
        hits = search_text(corpus, query, top_k=pool, exclude_kinds=exclude_kinds)
    else:
        hits = search_chunks(
            corpus, query, top_k=pool, rank=rank,
            exclude_kinds=exclude_kinds,
        )
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
    from .store.routing import sqlite_available

    rows = _rank_docs_sqlite(corpus) if sqlite_available(corpus.root) else []
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
    from .store.routing import sqlite_available
    if sqlite_available(corpus.root):
        rows = {r["doc_id"]: r for r in _rank_docs_sqlite(corpus)}
        return {
            did: {
                "citation_count": int(rows.get(did, {}).get("citation_count", 0)),
                "pagerank": float(rows.get(did, {}).get("pagerank", 0.0)),
            }
            for did in doc_ids
        }
    # No SQLite store available (hand-built test fixture or pre-build);
    # zeroed metrics are the expected fallback.
    return {did: {"citation_count": 0, "pagerank": 0.0} for did in doc_ids}


def _search_chunks_all_modes(
    corpus: Corpus, query: str, *,
    top_k: int, per_mode: int | None = None, in_doc: str | None = None,
    exclude_kinds: list[str] | None = None,
) -> list[dict]:
    """Run semantic + bm25 + literal-substring chunk search and dedupe.

    Each returned chunk dict carries a ``modes`` list (subset of
    ``{"semantic", "bm25", "text"}``) so callers can see which channels
    matched, and a ``score`` taken from the best-scoring mode for that
    chunk. Chunks present in more modes are surfaced first; ties broken
    by the RRF fusion across modes (k=60).

    Failures in any single mode are tolerated — e.g. an FTS5 syntax error
    in *query* (a stray hyphen, an unbalanced quote) drops just BM25 and
    the other channels still return.

    Optimization: opens one Store and embeds the query once; the three
    mode searches share both. The legacy per-mode helpers reopen the
    store each time, so we replicate their logic inline here.
    """
    from ..corpus.vectors_meta import read_meta
    from ..embedding import embedder_for
    from .store.fts import RRF_K_DEFAULT, rrf_fuse
    from .store.routing import active_space_id, open_store

    pool = per_mode or max(top_k * 2, 20)
    store = open_store(corpus.root)
    try:
        # Embed once; share between semantic and hybrid (we only need it for
        # the semantic side here; BM25 + text don't use it).
        meta = read_meta(corpus.sqlite_path)
        embed = embedder_for(meta.backend, meta.model, mode="query") if meta else None
        space_id = active_space_id(store)
        qv = embed([query])[0] if embed else None  # type: ignore[index]

        def _doc_ids_for(cids: list[str]) -> dict[str, str]:
            if not cids:
                return {}
            placeholders = ",".join("?" * len(cids))
            rows = store.con.execute(
                f"SELECT chunk_id, doc_id FROM chunks WHERE chunk_id IN ({placeholders})",
                cids,
            ).fetchall()
            return {r["chunk_id"]: r["doc_id"] for r in rows}

        def _semantic() -> list[dict]:
            if qv is None or not space_id:
                return []
            search_pool = pool * 4 if in_doc else pool
            hits = store.vector_index(space_id).search(qv, top_k=search_pool)
            if in_doc is not None:
                hits = _filter_hits_to_doc(store, hits, in_doc)[:pool]
            doc_map = _doc_ids_for([cid for cid, _ in hits])
            return [
                {"id": cid, "doc_id": doc_map.get(cid, ""), "score": float(score)}
                for cid, score in hits if cid in doc_map
            ]

        def _bm25() -> list[dict]:
            hits = store.search_chunks_bm25(query, top_k=pool, doc_id=in_doc)
            doc_map = _doc_ids_for([cid for cid, _ in hits])
            return [
                {"id": cid, "doc_id": doc_map.get(cid, ""), "score": float(score)}
                for cid, score in hits if cid in doc_map
            ]

        def _text() -> list[dict]:
            if in_doc is not None:
                rows = store.con.execute(
                    "SELECT chunk_id, doc_id FROM chunks WHERE doc_id = ? "
                    "AND LOWER(text) LIKE ? ORDER BY chunk_id LIMIT ?",
                    (in_doc, f"%{query.lower()}%", pool),
                ).fetchall()
            else:
                rows = store.con.execute(
                    "SELECT chunk_id, doc_id FROM chunks WHERE LOWER(text) LIKE ? "
                    "ORDER BY chunk_id LIMIT ?",
                    (f"%{query.lower()}%", pool),
                ).fetchall()
            return [{"id": r["chunk_id"], "doc_id": r["doc_id"]} for r in rows]

        sem_hits = _safe_mode("semantic", _semantic)
        bm_hits = _safe_mode("bm25", _bm25)
        text_hits = _safe_mode("text", _text)
    finally:
        store.close()

    if exclude_kinds:
        store2 = open_store(corpus.root)
        try:
            sem_pairs = [(h["id"], float(h.get("score", 0.0))) for h in sem_hits]
            bm_pairs = [(h["id"], float(h.get("score", 0.0))) for h in bm_hits]
            tx_pairs = [(h["id"], 0.0) for h in text_hits]
            sem_pairs = _filter_hits_excluding_kinds(store2, sem_pairs, exclude_kinds)
            bm_pairs = _filter_hits_excluding_kinds(store2, bm_pairs, exclude_kinds)
            tx_pairs = _filter_hits_excluding_kinds(store2, tx_pairs, exclude_kinds)
        finally:
            store2.close()
        keep_ids = {cid for cid, _ in sem_pairs} | {cid for cid, _ in bm_pairs} \
                   | {cid for cid, _ in tx_pairs}
        sem_hits = [h for h in sem_hits if h["id"] in keep_ids]
        bm_hits = [h for h in bm_hits if h["id"] in keep_ids]
        text_hits = [h for h in text_hits if h["id"] in keep_ids]

    by_id: dict[str, dict] = {}
    score_keys = (("semantic", "semantic_score"), ("bm25", "bm25_score"),
                  ("text", "text_score"))
    rankings_by_mode = {"semantic": sem_hits, "bm25": bm_hits, "text": text_hits}
    for mode, score_key in score_keys:
        for hit in rankings_by_mode[mode]:
            cid = hit["id"]
            entry = by_id.setdefault(cid, {
                "id": cid,
                "doc_id": hit.get("doc_id", ""),
                "modes": [],
            })
            if mode not in entry["modes"]:
                entry["modes"].append(mode)
            if "score" in hit:
                entry[score_key] = float(hit["score"])

    # Fuse across the three rankings; consensus wins via RRF.
    fused = rrf_fuse(
        [
            [(h["id"], float(h.get("score", 0.0))) for h in sem_hits],
            [(h["id"], float(h.get("score", 0.0))) for h in bm_hits],
            [(h["id"], 0.0) for h in text_hits],
        ],
        k=RRF_K_DEFAULT,
        top_k=top_k,
    )
    out: list[dict] = []
    for cid, fused_score in fused:
        entry = by_id.get(cid)
        if not entry:
            continue
        entry["score"] = round(float(fused_score), 6)
        out.append(entry)
    # Append any remaining entries (rare: a chunk only present in text
    # search may not survive RRF when others have stronger ranks).
    if len(out) < top_k:
        seen_ids = {e["id"] for e in out}
        for cid, entry in by_id.items():
            if cid in seen_ids:
                continue
            entry.setdefault("score", 0.0)
            out.append(entry)
            if len(out) >= top_k:
                break
    return out[:top_k]


def _safe_mode(mode: str, fn) -> list[dict]:
    try:
        return list(fn())
    except (QueryError, Exception):  # noqa: BLE001
        # FTS5 syntax error, embedder hiccup, etc. Drop just this mode.
        return []


def search_text(
    corpus: Corpus, needle: str, *,
    top_k: int = 50,
    exclude_kinds: list[str] | None = None,
) -> list[dict]:
    """Literal substring grep over chunk text.

    Uses SQLite `LIKE` against `wikify.db` when available (sub-ms for
    typical corpus sizes); falls back to scanning the on-disk JSONL
    only for hand-built fixtures with no SQLite store.

    ``exclude_kinds`` drops chunks whose ``section_type`` is in the
    list before returning -- the SQLite path filters in SQL, the
    JSONL fallback filters in Python.
    """
    from .store.routing import sqlite_available

    excluded = (
        {str(k).lower() for k in exclude_kinds} if exclude_kinds else set()
    )

    if sqlite_available(corpus.root):
        from .store.routing import open_store
        store = open_store(corpus.root)
        try:
            sql = (
                "SELECT chunk_id, doc_id, substr(text, 1, 160) AS preview, "
                "section_type, is_boilerplate "
                "FROM chunks WHERE LOWER(text) LIKE ? "
            )
            params: list = [f"%{needle.lower()}%"]
            if excluded:
                placeholders = ",".join("?" * len(excluded))
                sql += f"AND LOWER(section_type) NOT IN ({placeholders}) "
                params.extend(excluded)
            sql += "ORDER BY chunk_id LIMIT ?"
            params.append(top_k)
            rows = store.con.execute(sql, params).fetchall()
            return [
                {"id": r["chunk_id"], "doc_id": r["doc_id"], "preview": r["preview"]}
                | {
                    "section_type": r["section_type"] or "body",
                    "is_boilerplate": bool(r["is_boilerplate"]),
                }
                for r in rows
            ]
        finally:
            store.close()
    needle_lower = needle.lower()
    out: list[dict] = []
    for c in all_chunks(corpus):
        if needle_lower not in c.text.lower():
            continue
        if excluded and (c.section_type or "").lower() in excluded:
            continue
        out.append({
            "id": c.id,
            "doc_id": c.doc_id,
            "preview": c.text[:160],
            "section_type": c.section_type or "body",
            "is_boilerplate": bool(c.is_boilerplate),
        })
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
    field. The default form skips citation-index coverage so the call
    stays fast. Pass ``full=True`` to also report citation-marker
    indexing coverage (``traverse <chunk> --to cited-in-corpus``
    requires sources with populated ``ord_refs``).
    """
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    out: dict = {
        "root": str(corpus.root),
        "n_docs": len(docs),
        "n_chunks": len(chunks),
        "has_vectors": _has_vectors(corpus),
        "has_manifest": corpus.manifest_path.exists(),
        "has_sqlite_store": corpus.sqlite_path.exists(),
    }
    if out["has_sqlite_store"]:
        out.update(_sqlite_health(corpus, full=full))
    try:
        from .field_detect import detect_field, detect_field_scores

        out["field"] = detect_field(corpus)
        out["field_scores"] = detect_field_scores(corpus)[:5]
    except Exception as exc:
        out["field"] = None
        out["field_error"] = str(exc)
    if full and out["has_sqlite_store"]:
        try:
            out.update(_ord_refs_coverage(corpus, docs))
        except Exception as exc:
            out["ord_refs_coverage_pct"] = None
            out["ord_refs_error"] = str(exc)
    return out


def _has_vectors(corpus: Corpus) -> bool:
    """True iff the corpus has any chunk embeddings persisted in `wikify.db`."""
    if not corpus.sqlite_path.exists():
        return False
    import sqlite3
    try:
        con = sqlite3.connect(corpus.sqlite_path)
        try:
            row = con.execute(
                "SELECT 1 FROM embeddings WHERE node_type='chunk' LIMIT 1",
            ).fetchone()
            return row is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _sqlite_health(corpus: Corpus, *, full: bool) -> dict:
    """Probe wikify.db for schema sanity, FTS optimize state, and metric staleness."""
    from .store.metrics_global import is_stale
    from .store.routing import open_store

    try:
        store = open_store(corpus.root)
    except FileNotFoundError as exc:
        return {"sqlite_error": str(exc)}
    try:
        sqlite_check = store.con.execute("PRAGMA integrity_check").fetchone()[0]
        n_docs = store.con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_chunks = store.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_emb = store.con.execute(
            "SELECT COUNT(*) FROM embeddings WHERE node_type='chunk'",
        ).fetchone()[0]
        spaces = [
            dict(r) for r in store.con.execute(
                "SELECT space_id, dim, model FROM embedding_spaces",
            )
        ]
        out: dict = {
            "sqlite_integrity": sqlite_check,
            "sqlite_n_docs": n_docs,
            "sqlite_n_chunks": n_chunks,
            "sqlite_n_embeddings": n_emb,
            "sqlite_embedding_spaces": spaces,
            "metrics_corpus_citation_stale": is_stale(store.con, "corpus_citation"),
        }
        if full:
            out["sqlite_n_edges"] = store.con.execute(
                "SELECT COUNT(*) FROM graph_edges",
            ).fetchone()[0]
        return out
    finally:
        store.close()


def _ord_refs_coverage(corpus: Corpus, docs: list[Document]) -> dict:
    """Fraction of corpus docs that have at least one resolved bib_entry.

    Reads ``bib_entries.target_doc_id IS NOT NULL`` directly from
    ``wikify.db``. Returns zeros when ``wikify.db`` is absent.
    """
    from .store.routing import sqlite_available

    in_corpus_ids = {d.id for d in docs}
    n = len(in_corpus_ids)
    if not n or not sqlite_available(corpus.root):
        return {
            "sources_with_ord_refs": 0,
            "ord_refs_coverage_pct": 0.0,
        }
    from .store.routing import open_store
    store = open_store(corpus.root)
    try:
        rows = store.con.execute(
            "SELECT DISTINCT doc_id FROM bib_entries WHERE target_doc_id IS NOT NULL",
        )
        with_ord = sum(1 for r in rows if r[0] in in_corpus_ids)
    finally:
        store.close()
    return {
        "sources_with_ord_refs": with_ord,
        "ord_refs_coverage_pct": round(100.0 * with_ord / n, 1),
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

    meta = read_meta(corpus.sqlite_path)
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
_MULTI_RANK = "all"
_FIND_RANKS = {"semantic", *_LEXICAL_RANKS, _MULTI_RANK, *_SOURCE_RANKS, *_AUTHOR_RANKS}


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
        "--in-doc <doc-handle>": (
            "Scope chunk search to one document. Accepts any doc handle "
            "form (short, hex, or full id). BM25 / text get a cheap "
            "WHERE filter; vector search post-filters a wider pool."
        ),
        "--exclude-kind <kind>": (
            "Drop chunks whose section_type matches (repeatable). "
            "Kinds: abstract | introduction | background | methods | "
            "results | discussion | conclusion | references | "
            "acknowledgments | appendix | figure | table | caption | "
            "boilerplate | body. Typical: "
            "exclude_kinds=['references','acknowledgments'] to keep "
            "bibliography and acknowledgments paragraphs out of "
            "content retrieval."
        ),
    },
    "sample_strategies": {
        "diverse": (
            "Greedy submodular: PageRank prior + coverage gain over doc "
            "embeddings."
        ),
    },
    "walks": {
        "similarity_walk": {
            "purpose": (
                "Recursive cosine-similarity walk over chunk vectors. "
                "Starts from a query (top-k chunks at hop 0) or a single "
                "chunk handle and expands neighbours per hop."
            ),
            "params": {
                "query": "Concept seed (mutually exclusive with from_chunk).",
                "from_chunk": (
                    "chunk:<id-or-short> seed (mutually exclusive with query)."
                ),
                "depth": "Hops; 0 = seeds only.",
                "top_k": "Seed count at hop 0 (query mode only).",
                "neighbors": "Per-chunk fanout per hop.",
                "threshold": "Cosine cut; below this, edges are dropped.",
                "rank": "Hop-0 search method (query mode only).",
                "cross_doc_only": (
                    "True drops same-doc neighbours (default); False "
                    "includes intra-doc edges."
                ),
            },
            "result": (
                "{seeds, edges, chunks} -- chunks deduped across paths; "
                "edges typed 'similar' with cosine score."
            ),
        },
        "citation_walk": {
            "purpose": (
                "Concept-grounded recursive citation walk. For each "
                "frontier chunk, follow chunk_citations to in-corpus "
                "papers and pick that paper's best chunk for the same "
                "query (scoped to the doc), recursing to depth."
            ),
            "params": {
                "query": "Concept the walk is grounded on (required).",
                "depth": "Citation hops; 0 = seeds only.",
                "top_k": "Seed chunks at hop 0.",
                "rank": "Ranking method for seed and per-hop sub-search.",
            },
            "result": (
                "{seeds, edges, chunks} -- edges carry the citation "
                "marker that led from src_chunk to dst_chunk in dst_doc."
            ),
        },
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
    if by == "chunk" and rank not in {"semantic", _MULTI_RANK, *_LEXICAL_RANKS}:
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
    in_doc: str | None = None,
    exclude_kinds: list[str] | None = None,
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
            papers = search_papers(
                corpus, query, top_k=paper_pool, text=True,
                exclude_kinds=exclude_kinds,
            )
            return {
                "kind": "papers",
                "rows": _rerank_papers(corpus, papers, rank=rank, top_k=top_k),
                "scored": True,
            }
        return {
            "kind": "chunks",
            "rows": search_text(
                corpus, query, top_k=top_k, exclude_kinds=exclude_kinds,
            ),
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
            papers = search_papers(
                corpus, query, top_k=top_k, rank=rank,
                exclude_kinds=exclude_kinds,
            )
            return {"kind": "papers", "rows": papers, "scored": True}
        papers = search_papers(
            corpus, query, top_k=paper_pool, exclude_kinds=exclude_kinds,
        )
        return {
            "kind": "papers",
            "rows": _rerank_papers(corpus, papers, rank=rank, top_k=top_k),
            "scored": True,
        }

    return {
        "kind": "chunks",
        "rows": search_chunks(
            corpus, query, top_k=top_k, rank=rank, in_doc=in_doc,
            exclude_kinds=exclude_kinds,
        ),
        "scored": True,
    }


def similarity_walk(
    corpus: Corpus,
    *,
    query: str | None = None,
    from_chunk: str | None = None,
    depth: int = 2,
    top_k: int = 5,
    neighbors: int = 3,
    threshold: float = 0.65,
    rank: str = "all",
    cross_doc_only: bool = True,
) -> dict:
    """Concept- or chunk-seeded recursive cosine-similarity walk.

    Two seed modes (mutually exclusive):

    - *query* — find the top-`top_k` chunks for the concept (per
      *rank*), then expand each via cosine neighbours.
    - *from_chunk* — start from a single chunk handle; no search.

    Per hop, each chunk in the frontier emits up to *neighbors* edges
    to chunks with cosine ≥ *threshold*. Edges are typed
    ``kind="similar"`` and carry a ``score`` field. Chunks are
    deduped across paths (added once, at first encounter); subsequent
    edges to an already-visited chunk are dropped.

    With *cross_doc_only* (default True), neighbours in the same
    document as their source are filtered out — adjacent paragraphs
    are usually trivially similar.

    Returns ``{seeds, edges, chunks}`` matching `citation_walk`'s
    shape so callers can render either walker the same way.
    """
    import numpy as np

    from .store.routing import active_space_id, open_store, sqlite_available

    if (query is None) == (from_chunk is None):
        raise QueryError(
            "bad_seed",
            "similarity_walk requires exactly one of query / from_chunk",
        )
    if depth < 0:
        raise QueryError("bad_depth", f"depth must be >= 0; got {depth}")
    if top_k <= 0:
        raise QueryError("bad_top_k", f"top_k must be > 0; got {top_k}")
    if neighbors <= 0:
        raise QueryError("bad_neighbors", f"neighbors must be > 0; got {neighbors}")
    if not -1.0 <= threshold <= 1.0:
        raise QueryError("bad_threshold", f"threshold must be in [-1, 1]; got {threshold}")
    if not sqlite_available(corpus.root):
        raise QueryError(
            "no_wikify_db",
            "similarity-walk requires wikify.db; rebuild with `corpus build`",
        )

    store = open_store(corpus.root)
    try:
        # Vector matrix is the only thing this walker reads from SQLite at
        # depth>0; no graph_edges, no chunk_citations.
        space_id = active_space_id(store)
        if not space_id:
            raise QueryError(
                "no_embeddings",
                "corpus has no embedding space; run `corpus build` to embed chunks",
            )
        vi = store.vector_index(space_id)
        ids = vi.ids
        matrix = vi.matrix
        if matrix.shape[0] == 0:
            raise QueryError(
                "no_embeddings",
                "embedding space exists but has no chunks",
            )
        id_to_idx = {cid: i for i, cid in enumerate(ids)}

        # Seed
        chunks: dict[str, dict] = {}
        if query is not None:
            seeds = search_chunks(corpus, query, top_k=top_k, rank=rank)
            for s in seeds:
                chunks[s["id"]] = {**s, "hop": 0}
            seed_rows = [chunks[s["id"]] for s in seeds]
        else:
            short = (from_chunk or "").removeprefix("chunk:")
            try:
                cid = resolve_chunk_id(corpus, short)
            except (HandleNotFoundError, AmbiguousHandleError) as exc:
                raise QueryError("bad_chunk", str(exc)) from exc
            row = store.get_chunk(cid)
            if not row:
                raise QueryError("bad_chunk", f"chunk {from_chunk!r} not found")
            seed = {"id": cid, "doc_id": row["doc_id"], "hop": 0, "modes": []}
            chunks[cid] = seed
            seed_rows = [seed]

        edges: list[dict] = []
        if depth == 0:
            return {"seeds": seed_rows, "edges": edges, "chunks": chunks}

        # Cache doc_id per chunk_id; used by cross_doc_only and result rows.
        doc_cache: dict[str, str] = {s["id"]: s["doc_id"] for s in seed_rows}

        def _doc_of(cid: str) -> str:
            if cid not in doc_cache:
                row = store.get_chunk(cid)
                doc_cache[cid] = row["doc_id"] if row else ""
            return doc_cache[cid]

        frontier = [s["id"] for s in seed_rows]
        for hop in range(1, depth + 1):
            next_frontier: list[str] = []
            for src_id in frontier:
                src_idx = id_to_idx.get(src_id)
                if src_idx is None:
                    continue
                src_vec = matrix[src_idx]
                src_doc = _doc_of(src_id)
                sims = matrix @ src_vec  # cosine; vectors are unit-normalised
                # Sort all candidates desc; break early on threshold.
                order = np.argsort(-sims)
                added = 0
                for idx in order:
                    score = float(sims[int(idx)])
                    if score < threshold:
                        break  # rest are below
                    cid = ids[int(idx)]
                    if cid == src_id:
                        continue
                    if cross_doc_only and _doc_of(cid) == src_doc:
                        continue
                    if cid in chunks:
                        continue  # already visited — skip to keep the frontier focused
                    edges.append({
                        "src_chunk": src_id,
                        "dst_chunk": cid,
                        "kind": "similar",
                        "score": round(score, 6),
                        "hop": hop,
                    })
                    chunks[cid] = {
                        "id": cid,
                        "doc_id": _doc_of(cid),
                        "hop": hop,
                        "score": round(score, 6),
                    }
                    next_frontier.append(cid)
                    added += 1
                    if added >= neighbors:
                        break
            if not next_frontier:
                break
            frontier = next_frontier

        return {"seeds": seed_rows, "edges": edges, "chunks": chunks}
    finally:
        store.close()


def citation_walk(
    corpus: Corpus,
    *,
    query: str,
    depth: int = 2,
    top_k: int = 5,
    rank: str = "all",
) -> dict:
    """Recursive concept-grounded citation traversal.

    Hop 0: find top-`top_k` chunks for *query* across the corpus.
    Hops 1..depth: for each chunk found, follow `chunk_citations` to
    the in-corpus papers it cites; for each cited paper, find its
    best chunk for the same query (scoped to that doc) and recurse.

    Returns a dict with:

    - ``seeds``: top-`top_k` chunks at hop 0 (each row carries the
      same fields as ``find`` plus ``hop=0``).
    - ``edges``: list of ``{src_chunk, marker, dst_chunk, dst_doc, hop}``
      records describing the citation lineage.
    - ``chunks``: dict ``{chunk_id: chunk_dict}`` deduped across hops;
      each value carries ``hop``, ``doc_id``, ``score``, ``modes``
      (when produced by `--rank all`), and a short `preview`.

    Pruning: a chunk is added once. Subsequent paths reaching it via a
    different citation just attach a new `edges` row without recursing.
    """
    from .store.routing import open_store, sqlite_available

    if not sqlite_available(corpus.root):
        raise QueryError(
            "no_wikify_db",
            "citation-walk requires wikify.db; rebuild with `corpus build`",
        )
    if depth < 0:
        raise QueryError("bad_depth", f"depth must be >= 0; got {depth}")
    if top_k <= 0:
        raise QueryError("bad_top_k", f"top_k must be > 0; got {top_k}")

    # Hop 0: corpus-wide search.
    seed_rows = search_chunks(corpus, query, top_k=top_k, rank=rank)
    chunks: dict[str, dict] = {}
    edges: list[dict] = []

    def _attach_chunk(row: dict, hop: int) -> None:
        cid = row["id"]
        if cid in chunks:
            return
        chunks[cid] = {**row, "hop": hop}

    for r in seed_rows:
        _attach_chunk(r, hop=0)

    if depth == 0 or not seed_rows:
        return {
            "seeds": [chunks[r["id"]] for r in seed_rows],
            "edges": edges,
            "chunks": chunks,
        }

    store = open_store(corpus.root)
    try:
        frontier = [r["id"] for r in seed_rows]
        for hop in range(1, depth + 1):
            next_frontier: list[str] = []
            placeholders = ",".join("?" * len(frontier))
            cite_rows = list(store.con.execute(
                f"SELECT cc.chunk_id, cc.marker_text, be.target_doc_id "
                f"FROM chunk_citations cc JOIN bib_entries be USING (bib_id) "
                f"WHERE cc.chunk_id IN ({placeholders}) "
                f"AND be.target_doc_id IS NOT NULL",
                frontier,
            ))
            # Group target_doc_ids per source chunk.
            citations_per_src: dict[str, list[tuple[str, str]]] = {}
            for r in cite_rows:
                citations_per_src.setdefault(r["chunk_id"], []).append(
                    (r["marker_text"] or "", r["target_doc_id"]),
                )
            for src_chunk_id, items in citations_per_src.items():
                # Dedupe target docs (multiple markers can point at the same paper)
                seen_targets: set[str] = set()
                for marker, target_doc in items:
                    if target_doc in seen_targets:
                        continue
                    seen_targets.add(target_doc)
                    sub = search_chunks(
                        corpus, query, top_k=1, rank=rank, in_doc=target_doc,
                    )
                    if not sub:
                        # No semantic/bm25 hit inside that doc; skip rather
                        # than emit an arbitrary chunk.
                        continue
                    dst = sub[0]
                    edges.append({
                        "src_chunk": src_chunk_id,
                        "marker": marker,
                        "dst_chunk": dst["id"],
                        "dst_doc": target_doc,
                        "hop": hop,
                    })
                    if dst["id"] not in chunks:
                        _attach_chunk(dst, hop=hop)
                        next_frontier.append(dst["id"])
            if not next_frontier:
                break
            frontier = next_frontier
    finally:
        store.close()

    return {
        "seeds": [chunks[r["id"]] for r in seed_rows],
        "edges": edges,
        "chunks": chunks,
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
