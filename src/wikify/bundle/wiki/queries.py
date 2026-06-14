"""Read-only wiki query helpers — the surface ``cli/wiki.py`` calls.

Walks the on-disk wiki tree (``wiki/articles/`` + ``wiki/people/``).
Heavy graph + page-vector queries (``wiki/graph.py``) stay accessible
through the underlying fluent KG; this module wires up the file-walk
queries the CLI uses by default.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from ...api import Bundle
from .graph import load_wiki_graph

_FIND_MODES = {"text", "bm25", "semantic", "hybrid"}


def page_path(bundle: Bundle, *, slug: str, kind: str) -> Path:
    sub = bundle.wiki_articles_dir if kind == "article" else bundle.wiki_people_dir
    return sub / f"{slug}.md"


def list_articles(bundle: Bundle) -> list[str]:
    if not bundle.wiki_articles_dir.is_dir():
        return []
    return sorted(p.stem for p in bundle.wiki_articles_dir.glob("*.md"))


def list_people(bundle: Bundle) -> list[str]:
    if not bundle.wiki_people_dir.is_dir():
        return []
    return sorted(p.stem for p in bundle.wiki_people_dir.glob("*.md"))


def list_files(bundle: Bundle) -> list[str]:
    if not bundle.wiki_dir.is_dir():
        return []
    out: list[str] = []
    for p in sorted(bundle.wiki_dir.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(bundle.root)).replace("\\", "/"))
    return out


def _page_hit_from_row(bundle: Bundle, row: Any, *, score: float | None = None) -> dict:
    slug = row["slug"]
    kind = row["kind"]
    body = row["body"] or ""
    return {
        "page_id": row["page_id"],
        "slug": slug,
        "kind": kind,
        "title": row["title"] or slug,
        "path": str(
            page_path(bundle, slug=slug, kind=kind).relative_to(bundle.root)
        ).replace("\\", "/"),
        "snippet": body[:160].replace("\n", " "),
        **({"score": score} if score is not None else {}),
    }


def _bm25_page_scores(bundle: Bundle, query: str, *, top_k: int) -> list[tuple[str, float]]:
    if not bundle.sqlite_path.exists():
        return []
    from .store import open_wiki_store, search_wiki_bm25

    con = open_wiki_store(bundle.sqlite_path)
    try:
        return search_wiki_bm25(con, query, top_k=top_k)
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _page_hits_for_scores(
    bundle: Bundle,
    scores: list[tuple[str, float]],
    *,
    modes_by_page: dict[str, list[str]] | None = None,
) -> list[dict]:
    if not scores:
        return []
    if not bundle.sqlite_path.exists():
        rows = _page_rows_for_ids(bundle, [page_id for page_id, _ in scores])
        score_by_page = dict(scores)
        hits: list[dict] = []
        for row in rows:
            page_id = str(row.get("id") or row.get("page_id") or "")
            hit = {
                "page_id": page_id,
                "slug": row.get("slug", page_id),
                "kind": row.get("kind", ""),
                "title": row.get("title", page_id),
                "path": row.get("path", ""),
                "snippet": row.get("snippet", ""),
                "score": score_by_page.get(page_id, 0.0),
            }
            if modes_by_page is not None:
                hit["modes"] = modes_by_page.get(page_id, [])
            hits.append(hit)
        return hits
    from .store import open_wiki_store

    con = open_wiki_store(bundle.sqlite_path)
    try:
        rows: list[dict] = []
        for page_id, score in scores:
            r = con.execute(
                "SELECT page_id, slug, kind, title, body FROM wiki_pages WHERE page_id = ?",
                (page_id,),
            ).fetchone()
            if r:
                hit = _page_hit_from_row(bundle, r, score=score)
                if modes_by_page is not None:
                    hit["modes"] = modes_by_page.get(page_id, [])
                rows.append(hit)
        return rows
    finally:
        con.close()


def find_bm25(bundle: Bundle, query: str, *, top_k: int = 50) -> list[dict]:
    """BM25 search over committed wiki pages in ``wiki.db``."""
    return _page_hits_for_scores(
        bundle,
        _bm25_page_scores(bundle, query, top_k=top_k),
        modes_by_page={},
    )


def _embed_wiki_query(query: str, space: dict[str, Any] | None = None):
    if space is not None:
        from ...embedding import embedder_for

        embed = embedder_for(
            str(space["backend"]),
            space.get("model"),
            mode="query",
        )
        vecs = embed([query])
    else:
        from ...embedding import embed_queries

        vecs = embed_queries([query])
    if getattr(vecs, "shape", (0,))[0] == 0:
        return None
    return vecs[0]


def _load_page_vectors(bundle: Bundle):
    if bundle.sqlite_path.exists():
        from ...corpus.store.vectors import decode_vector
        from ...corpus.vectors import VectorStore
        from .store import open_wiki_store

        con = open_wiki_store(bundle.sqlite_path)
        try:
            space = con.execute(
                "SELECT space_id, backend, model, dim FROM wiki_embedding_spaces "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if space:
                space_meta = dict(space)
                rows = con.execute(
                    "SELECT page_id, vector FROM wiki_embeddings "
                    "WHERE space_id = ? ORDER BY page_id",
                    (space["space_id"],),
                ).fetchall()
                if rows:
                    import numpy as np

                    matrix = np.vstack(
                        [decode_vector(r["vector"], int(space["dim"])) for r in rows]
                    ).astype("float32")
                    return (
                        VectorStore(ids=[r["page_id"] for r in rows], matrix=matrix),
                        space_meta,
                    )
        finally:
            con.close()
    if not bundle.derived_vectors_path.exists():
        return None, None
    from ...corpus.vectors import load_vectors

    return load_vectors(bundle.derived_vectors_path), None


def _semantic_page_scores(bundle: Bundle, query: str, *, top_k: int) -> list[tuple[str, float]]:
    if not query.strip():
        return []
    try:
        vectors, space = _load_page_vectors(bundle)
        if vectors is None:
            return []
        if vectors.matrix.shape[0] == 0:
            return []
        qvec = _embed_wiki_query(query, space)
        if qvec is None:
            return []
        return vectors.topk(qvec, top_k)
    except (OSError, ValueError, ImportError, RuntimeError):
        return []


def find_semantic(bundle: Bundle, query: str, *, top_k: int = 50) -> list[dict]:
    """Semantic search over ``derived/vectors.npz`` page embeddings."""
    return _page_hits_for_scores(
        bundle,
        _semantic_page_scores(bundle, query, top_k=top_k),
        modes_by_page={},
    )


def find_hybrid(bundle: Bundle, query: str, *, top_k: int = 50) -> list[dict]:
    """RRF fusion over wiki BM25 and page-vector semantic search."""
    from ...corpus.store.fts import RRF_K_DEFAULT, rrf_fuse

    pool = max(top_k * 2, 20)
    bm25_scores = _bm25_page_scores(bundle, query, top_k=pool)
    semantic_scores = _semantic_page_scores(bundle, query, top_k=pool)
    rankings = [scores for scores in (bm25_scores, semantic_scores) if scores]
    if not rankings:
        return find_text(bundle, query, top_k=top_k)
    modes_by_page: dict[str, list[str]] = {}
    for mode, scores in (("bm25", bm25_scores), ("semantic", semantic_scores)):
        for page_id, _ in scores:
            modes_by_page.setdefault(page_id, []).append(mode)
    fused = rrf_fuse(rankings, k=RRF_K_DEFAULT, top_k=top_k)
    return _page_hits_for_scores(bundle, fused, modes_by_page=modes_by_page)


def find(bundle: Bundle, query: str, *, mode: str = "hybrid", top_k: int = 50) -> list[dict]:
    """Search committed wiki pages by text, BM25, semantic, or hybrid mode."""
    if mode not in _FIND_MODES:
        raise ValueError(
            f"unknown wiki find mode {mode!r}; expected "
            f"{' | '.join(sorted(_FIND_MODES))}"
        )
    if mode == "text":
        return find_text(bundle, query, top_k=top_k)
    if mode == "bm25":
        return find_bm25(bundle, query, top_k=top_k)
    if mode == "semantic":
        return find_semantic(bundle, query, top_k=top_k)
    return find_hybrid(bundle, query, top_k=top_k)


def find_text(bundle: Bundle, needle: str, *, top_k: int = 50) -> list[dict]:
    """Literal substring grep over committed page bodies."""
    out: list[dict] = []
    needle_lower = needle.lower()
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            if needle_lower in text.lower():
                snippet = ""
                idx = text.lower().find(needle_lower)
                if idx >= 0:
                    snippet = text[max(0, idx - 40) : idx + 120].replace("\n", " ")
                out.append(
                    {
                        "slug": p.stem,
                        "kind": kind,
                        "title": p.stem,
                        "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                        "snippet": snippet,
                    }
                )
                if len(out) >= top_k:
                    return out
    return out


class AmbiguousSlugError(LookupError):
    """The given short slug matches more than one committed page."""

    def __init__(self, short: str, matches: list[str]) -> None:
        super().__init__(
            f"slug {short!r} is ambiguous; matches: {', '.join(matches[:5])}"
            + (f" (+{len(matches) - 5} more)" if len(matches) > 5 else "")
        )
        self.short = short
        self.matches = matches


def _normalize_handle(text: str) -> str:
    """Casefold and collapse separators so titles and file slugs compare equal.

    Wiki pages are named by their title, but the on-disk filename
    convention has changed over time: current bundles keep spaces
    (``Atomic Layer Deposition.md``) while older bundles used kebab-case
    (``atomic-layer-deposition.md``). Both name the same concept. This
    normalisation maps runs of whitespace, hyphens, and underscores to a
    single space and casefolds, so a handle resolves regardless of which
    convention produced the file.
    """
    s = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"[\s_-]+", " ", s).strip()


def resolve_slug(bundle: Bundle, short: str) -> tuple[str, str] | None:
    """Resolve a handle to ``(slug, kind)`` against committed pages.

    Tier 1: exact filename-stem match in articles/ then people/.
    Tier 2: case- and separator-insensitive exact match (so the natural
    title ``"Atomic Layer Deposition"`` resolves a kebab-case
    ``atomic-layer-deposition.md`` file and vice versa).
    Tier 3: case-insensitive prefix match if unique.
    Always returns the page's real on-disk stem so emitted handles
    round-trip. Returns ``None`` for an empty handle or no match; raises
    ``AmbiguousSlugError`` when a tier matches more than one page. A
    collision spanning both an article and a person (same normalized
    title) is treated as ambiguous, except an exact filename match which
    resolves article-first.

    Stem comparison is done in Python (case-sensitive) rather than via
    ``Path.is_file`` so a case-insensitive filesystem cannot report the
    queried casing as the slug instead of the true filename.
    """
    if not short.strip():
        return None
    norm_target = _normalize_handle(short)
    short_l = short.lower()
    exact_matches: list[tuple[str, str]] = []
    norm_matches: list[tuple[str, str]] = []
    prefix_matches: list[tuple[str, str]] = []
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            stem = p.stem
            if stem == short:
                exact_matches.append((stem, kind))
            if _normalize_handle(stem) == norm_target:
                norm_matches.append((stem, kind))
            if stem.lower().startswith(short_l):
                prefix_matches.append((stem, kind))
    if exact_matches:
        return exact_matches[0]
    if len(norm_matches) == 1:
        return norm_matches[0]
    if len(norm_matches) > 1:
        raise AmbiguousSlugError(short, [s for s, _ in norm_matches])
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise AmbiguousSlugError(short, [s for s, _ in prefix_matches])
    return None


_PAGE_RELATIONS = {
    "links",
    "linked-by",
    "co-evidence",
    "evidence",
    "similar",
    "see-also",
    "category",
    "categories",
}
_CATEGORY_RELATIONS = {"children", "parent", "pages"}
_WIKI_RELATIONS = _PAGE_RELATIONS | _CATEGORY_RELATIONS


def _navigation_groups(bundle: Bundle) -> dict[str, dict]:
    payload: dict[str, Any] | None = None
    if bundle.sqlite_path.is_file():
        try:
            from .store import export_navigation_json, list_wiki_categories, open_wiki_store

            con = open_wiki_store(bundle.sqlite_path)
            try:
                if list_wiki_categories(con):
                    payload = export_navigation_json(con)
            finally:
                con.close()
        except (OSError, sqlite3.Error, ImportError):
            payload = None
    path = bundle.derived_dir / "navigation.json"
    if payload is None:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    groups: dict[str, dict] = {}

    def walk(items: list, parent: str | None = None) -> None:
        for raw in items:
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("id") or "").strip()
            if not cid:
                continue
            children = raw.get("children") if isinstance(raw.get("children"), list) else []
            groups[cid] = {
                "id": cid,
                "type": "category",
                "title": str(raw.get("title") or cid),
                "description": str(raw.get("description") or ""),
                "parent": parent or "",
                "page_ids": [str(pid) for pid in (raw.get("page_ids") or [])],
                "children": [
                    str(child.get("id"))
                    for child in children
                    if isinstance(child, dict) and child.get("id")
                ],
            }
            walk(children, cid)

    walk(payload.get("groups") if isinstance(payload.get("groups"), list) else [])
    for group in groups.values():
        group["n_pages"] = len(group["page_ids"])
        group["n_children"] = len(group["children"])
    return groups


def _category_public_row(group: dict) -> dict:
    return {k: v for k, v in group.items() if k not in {"page_ids", "children"}}


def _category_rows_for_page(bundle: Bundle, page_id: str) -> list[dict]:
    rows = [
        _category_public_row(group)
        for group in _navigation_groups(bundle).values()
        if page_id in group["page_ids"]
    ]
    return sorted(rows, key=lambda item: str(item["id"]))


def traverse_category(
    bundle: Bundle,
    *,
    category_id: str,
    relation: str,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from a ``category:<id>`` navigation handle."""
    if relation not in _CATEGORY_RELATIONS:
        raise ValueError(
            f"unknown category relation {relation!r}; expected "
            f"{' | '.join(sorted(_CATEGORY_RELATIONS))}"
        )
    groups = _navigation_groups(bundle)
    group = groups.get(category_id)
    if group is None:
        return []
    if relation == "children":
        rows = [
            _category_public_row(groups[cid])
            for cid in group["children"]
            if cid in groups
        ]
    elif relation == "parent":
        parent = group.get("parent")
        rows = [_category_public_row(groups[parent])] if parent in groups else []
    else:
        rows = _page_rows_for_ids(bundle, group["page_ids"])
    return rows[:top_k] if top_k is not None else rows


def _slug_to_page_id(bundle: Bundle, slug: str) -> str | None:
    """Resolve a filename slug to the page's frontmatter ``id``.

    The wiki graph keys nodes by ``page.id`` (from frontmatter, falling
    back to the filename stem). When ``id`` differs from the slug the
    CLI passes (e.g. legacy ``id: concept-photocatalysis`` for slug
    ``Photocatalysis``), graph lookups by slug miss. This helper finds
    the on-disk page whose stem matches *slug* and returns its real id.
    """
    from .page import parse_page

    for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
        if not sub.is_dir():
            continue
        candidate = sub / f"{slug}.md"
        if candidate.is_file():
            try:
                return parse_page(candidate).id
            except (OSError, ValueError):
                return slug
    return None


def _page_id_to_slug_map(bundle: Bundle) -> dict[str, str]:
    """Reverse map ``page.id -> filename slug`` for handle round-tripping.

    Page-typed traverse outputs (``links``, ``linked-by``, ``co-evidence``)
    must emit handles that ``wiki show`` can resolve, and ``show``
    resolves by filename slug. The graph keys by frontmatter ``id``,
    so we walk the article + person dirs once and build the inverse.
    """
    from .page import parse_page

    out: dict[str, str] = {}
    for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            try:
                pid = parse_page(p).id
            except (OSError, ValueError):
                continue
            # First-seen wins; duplicate ids across kinds shouldn't exist
            # but if they do, articles take precedence by iteration order.
            out.setdefault(pid, p.stem)
    return out


def _page_rows_for_ids(bundle: Bundle, page_ids: list[str]) -> list[dict]:
    if not page_ids:
        return []
    from .page import parse_page

    wanted = set(page_ids)
    rows: list[dict] = []
    for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
        if not sub.is_dir():
            continue
        for path in sorted(sub.glob("*.md")):
            try:
                page = parse_page(path)
            except (OSError, ValueError):
                continue
            if page.id not in wanted:
                continue
            rows.append({
                "id": page.id,
                "type": "page",
                "slug": path.stem,
                "kind": page.kind,
                "title": page.title,
                "path": str(path.relative_to(bundle.root)).replace("\\", "/"),
                "snippet": (page.body_clean or "")[:160].replace("\n", " "),
                "n_links": len(page.links or []),
                "n_evidence": len(page.evidence or []),
            })
    order = {pid: i for i, pid in enumerate(page_ids)}
    rows.sort(key=lambda r: order.get(str(r.get("id", "")), len(order)))
    return rows


def _similar_page_ids(
    bundle: Bundle,
    page_id: str,
    *,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    try:
        vectors, _space = _load_page_vectors(bundle)
        if vectors is None:
            return []
        try:
            seed = vectors.vector(page_id)
        except KeyError:
            return []
        hits = vectors.topk(seed, (top_k or 20) + 1)
        return [(pid, score) for pid, score in hits if pid != page_id][: top_k or None]
    except (OSError, ValueError, ImportError, KeyError):
        return []


def traverse_page(
    bundle: Bundle,
    *,
    slug: str,
    relation: str,
    rank: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Traverse one hop from a wiki page slug.

    Page-typed relations (``links``, ``linked-by``, ``co-evidence``)
    return ``{slug, kind, title, n_evidence, n_links}`` rows. The
    ``evidence`` relation returns ``{chunk_id, doc_id, quote, page_id}``
    rows so callers can pipe ``chunk_id`` into corpus traversals.
    """
    if relation not in _PAGE_RELATIONS:
        raise ValueError(
            f"unknown wiki relation {relation!r}; expected "
            f"{' | '.join(sorted(_PAGE_RELATIONS))}"
        )
    page_id = _slug_to_page_id(bundle, slug)
    if relation in {"category", "categories"}:
        return _category_rows_for_page(bundle, page_id or slug)
    if relation == "similar":
        similar = _similar_page_ids(bundle, page_id or slug, top_k=top_k)
        if similar:
            rows = _page_rows_for_ids(bundle, [pid for pid, _ in similar])
            scores = dict(similar)
            for row in rows:
                row["score"] = scores.get(str(row.get("id", "")), 0.0)
            return rows
    if not bundle.sqlite_path.is_file():
        return []
    wkg = load_wiki_graph(bundle.sqlite_path)
    backend = wkg._backend
    if page_id is None or not backend.has_node(page_id):
        return []
    qb = wkg.page(page_id)
    if relation == "links":
        result = qb.links()
    elif relation == "linked-by":
        result = qb.linked_by()
    elif relation == "co-evidence":
        result = qb.co_evidence()
    elif relation == "evidence":
        result = qb.evidence()
    else:  # see-also
        ids = sorted(
            set(qb.links().ids())
            | set(qb.linked_by().ids())
            | set(qb.co_evidence().ids())
        )
        rows = _page_rows_for_ids(bundle, ids)
        return rows[:top_k] if top_k is not None else rows
    # Page-typed rows must round-trip through `wiki show`, which resolves
    # by filename slug. Build the id->slug map once per call.
    id_to_slug = (
        _page_id_to_slug_map(bundle)
        if relation in {"links", "linked-by", "co-evidence"} else {}
    )
    rows: list[dict] = []
    for nid in result.ids():
        if not backend.has_node(nid):
            continue
        attrs = backend.G.nodes[nid]
        ntype = attrs.get("type", "")
        if ntype == "page":
            rows.append({
                "id": nid,
                "type": "page",
                "slug": id_to_slug.get(nid, nid),
                "kind": attrs.get("kind", ""),
                "title": attrs.get("title", ""),
                "n_links": int(attrs.get("n_links", 0) or 0),
                "n_evidence": int(attrs.get("n_evidence", 0) or 0),
            })
        elif ntype == "evidence":
            rows.append({
                "id": nid,
                "type": "evidence",
                "page_id": attrs.get("page_id", ""),
                "chunk_id": attrs.get("chunk_id", ""),
                "doc_id": attrs.get("doc_id", ""),
                "quote": attrs.get("quote", ""),
            })
    if rank in {"n_links", "n_evidence"}:
        rows.sort(key=lambda r: (-int(r.get(rank, 0) or 0), str(r.get("id", ""))))
    elif rank is not None:
        raise ValueError(
            f"unknown wiki rank {rank!r}; expected n_links | n_evidence"
        )
    if top_k is not None:
        rows = rows[:top_k]
    return rows


def page_title(path: Path, slug: str) -> str:
    """Return a page's frontmatter title, falling back to *slug*."""
    from .page import parse_page

    try:
        return parse_page(path).title or slug
    except (OSError, ValueError):
        return slug


def show_page(bundle: Bundle, *, handle: str) -> dict | None:
    """Return ``{"path", "kind", "slug", "title", "text"}`` for one page handle.

    The handle may be:

    - a relative file path within the bundle root,
    - an exact slug (article first, then person),
    - the natural title (case- and separator-insensitive),
    - a unique case-insensitive prefix of a slug.

    Raises ``AmbiguousSlugError`` when a handle matches more than one page.
    """
    # Direct path?
    candidate = bundle.root / handle
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
        kind = "article" if "wiki/articles" in handle.replace("\\", "/") else "person"
        return {
            "path": str(candidate.relative_to(bundle.root)).replace("\\", "/"),
            "kind": kind,
            "slug": candidate.stem,
            "title": page_title(candidate, candidate.stem),
            "text": text,
        }
    resolved = resolve_slug(bundle, handle)
    if resolved is None:
        return None
    slug, kind = resolved
    p = page_path(bundle, slug=slug, kind=kind)
    return {
        "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
        "kind": kind,
        "slug": slug,
        "title": page_title(p, slug),
        "text": p.read_text(encoding="utf-8"),
    }
