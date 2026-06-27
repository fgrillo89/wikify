"""``derived/`` projections — rebuildable machine views of the committed wiki.

Three projections:

- ``derived/index.json``    list of every committed page (slug + path + kind).
- ``derived/graph.json``    serialised wiki knowledge graph (cite edges
                             between pages). Rebuilt via the existing
                             ``bundle/wiki/graph.py`` helpers.
- ``derived/vectors.npz``   per-page embeddings, used for ``wiki find``.

The graph + vectors rebuild reads every markdown-authored page
(``wiki/articles/`` + ``wiki/people/``) and reconstructs the graph from the
``[^eN]`` evidence footnotes in each page body. Data pages are authored from
the claim store, not re-derived here. The
``bundle/wiki/graph.py`` helpers do the heavy lifting; this module
adapts them to the bundle's ``derived_*`` paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...api import Bundle


def _committed_page_dirs(bundle: Bundle) -> tuple[tuple[str, Path], ...]:
    """The (kind, dir) pairs for every committed-page kind — the listing /
    index surface. Data artifacts are first-class committed pages here."""
    return (
        ("article", bundle.wiki_articles_dir),
        ("person", bundle.wiki_people_dir),
        ("data", bundle.wiki_data_dir),
    )


# The markdown-authored kinds. Data pages are deliberately excluded: their
# wiki.db row + evidence are authored from the claim store by
# ``register_artifact_wiki_page`` (lossless chunk ids), whereas their rendered
# markdown only carries doc-level references — re-deriving a data page from its
# markdown would overwrite precise ``wiki_evidence.chunk_id`` values with doc
# ids. So a markdown rebuild (graph + vectors) walks articles + people only.
def _markdown_page_dirs(bundle: Bundle) -> tuple[tuple[str, Path], ...]:
    return (
        ("article", bundle.wiki_articles_dir),
        ("person", bundle.wiki_people_dir),
    )


def list_committed_pages(bundle: Bundle) -> list[dict]:
    """Walk every committed-page kind and return per-page metadata."""
    out: list[dict] = []
    for kind, sub in _committed_page_dirs(bundle):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            out.append(
                {
                    "kind": kind,
                    "slug": p.stem,
                    "path": str(p.relative_to(bundle.root)).replace("\\", "/"),
                }
            )
    return out


def rebuild_index(bundle: Bundle) -> Path:
    """Write ``derived/index.json`` with every committed page slug + path."""
    bundle.derived_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "pages": list_committed_pages(bundle),
    }
    bundle.derived_index_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return bundle.derived_index_path


def read_index(bundle: Bundle) -> dict:
    if not bundle.derived_index_path.exists():
        return {"schema_version": 1, "pages": []}
    return json.loads(bundle.derived_index_path.read_text(encoding="utf-8"))


def _load_pages(bundle: Bundle) -> list:
    """Walk the markdown-authored page kinds and return parsed WikiPages.

    Feeds the embedding vector build, so this deliberately covers only the
    prose concept pages (articles + people). Data-artifact pages are pivot
    tables, not prose, and are intentionally excluded from the semantic vector
    space — they stay first-class via BM25/FTS (`find_text`/`find_hybrid`),
    show, traverse, index, and navigation. See `queries.find_semantic`.
    """
    from .page import parse_page

    out: list = []
    for _kind, sub in _markdown_page_dirs(bundle):
        if not sub.is_dir():
            continue
        for p in sorted(sub.glob("*.md")):
            page = parse_page(p)
            # ``parse_page`` returns a Page (Bundle-side dataclass);
            # convert to WikiPage shape for build_wiki_graph.
            from ...models import Evidence, WikiPage

            evidence = [
                Evidence(
                    marker=ev.marker,
                    chunk_id=ev.chunk_id or "",
                    doc_id=ev.doc_id or "",
                    quote=ev.quote or "",
                )
                for ev in (page.evidence or [])
            ]
            out.append(
                WikiPage(
                    id=page.id,
                    kind=page.kind,
                    title=page.title,
                    aliases=list(page.aliases or []),
                    body_markdown=page.body_clean or "",
                    evidence=evidence,
                    links=list(page.links or []),
                    figures=list(page.figures or []),
                )
            )
    return out


def rebuild_graph(bundle: Bundle) -> Path:
    """Refresh `wiki.db` rows from the markdown-authored pages on disk.

    Walks `wiki/articles/` + `wiki/people/`, parses each markdown file, and
    upserts the result into `wiki.db`. Data pages are excluded — they are
    authored from the claim store by `register_artifact_wiki_page`, not
    re-derived from their lossy rendered markdown. The wiki graph IS wiki.db;
    `derived/graph.json` is no longer produced.
    """
    from .page import parse_page
    from .store import open_wiki_store, upsert_wiki_page

    con = open_wiki_store(bundle.sqlite_path)
    try:
        for _kind, sub in _markdown_page_dirs(bundle):
            if not sub.is_dir():
                continue
            for path in sorted(sub.glob("*.md")):
                page = parse_page(path)
                upsert_wiki_page(
                    con,
                    page_id=page.id,
                    slug=path.stem,
                    title=page.title or page.id,
                    kind=page.kind,
                    body=page.body_clean or "",
                    frontmatter={"aliases": list(page.aliases or [])},
                    evidence=[
                        {
                            "marker": ev.marker,
                            "chunk_id": ev.chunk_id or "",
                            "doc_id": ev.doc_id or "",
                            "quote": ev.quote or "",
                        }
                        for ev in (page.evidence or [])
                    ],
                    links=[
                        link for link in (page.links or []) if link != page.id
                    ],
                )
    finally:
        con.close()
    # Data pages are authored from the claim store (lossless chunk ids), not
    # re-derived from their lossy markdown — restore/refresh their rows here so
    # `wiki rebuild` recovers data pages even when wiki.db was deleted/stale.
    from ...data.artifact_page import register_committed_data_pages

    register_committed_data_pages(bundle)
    return bundle.sqlite_path


def rebuild_vectors(bundle: Bundle) -> Path:
    """Rebuild committed-page embeddings.

    ``wiki.db`` is the canonical query store. ``derived/vectors.npz`` is
    still written as a compatibility projection for older readers.

    Held under the bundle run lock so the delete-and-replace of
    ``wiki_embeddings`` cannot race a concurrent commit's incremental embed
    (which also locks) and silently drop the just-committed page's vector.
    """
    import os

    from ..run.lock import run_lock

    bundle.derived_dir.mkdir(parents=True, exist_ok=True)
    with run_lock(bundle, owner=f"rebuild-vectors/pid-{os.getpid()}", ttl_seconds=600):
        return _rebuild_vectors_locked(bundle)


def _rebuild_vectors_locked(bundle: Bundle) -> Path:
    from ...corpus.vectors import save_vectors
    from ...embedding import current_backend, embed_passages
    from .graph import build_wiki_vectors
    from .store import (
        open_wiki_store,
        upsert_wiki_embedding_space,
        upsert_wiki_embeddings,
    )

    rebuild_graph(bundle)
    pages = _load_pages(bundle)
    cfg = current_backend()
    space_id = _wiki_space_id(cfg)
    if not pages:
        # No committed pages — write an empty vectors file so callers can
        # still ``np.load`` without branching.
        import numpy as _np

        from ...corpus.vectors import VectorStore

        empty = VectorStore(matrix=_np.zeros((0, 0), dtype="float32"), ids=[])
        save_vectors(bundle.derived_vectors_path, empty)
        con = open_wiki_store(bundle.sqlite_path)
        try:
            upsert_wiki_embedding_space(
                con,
                space_id,
                str(cfg["backend"]),
                cfg.get("model") if isinstance(cfg.get("model"), str) else None,
                int(cfg["dim"] or 0),
            )
            con.execute("DELETE FROM wiki_embeddings WHERE space_id = ?", (space_id,))
        finally:
            con.close()
        return bundle.derived_vectors_path
    vectors = build_wiki_vectors(pages, embed_passages)
    save_vectors(bundle.derived_vectors_path, vectors)
    con = open_wiki_store(bundle.sqlite_path)
    try:
        dim = int(vectors.matrix.shape[1] if vectors.matrix.ndim == 2 else cfg["dim"] or 0)
        upsert_wiki_embedding_space(
            con,
            space_id,
            str(cfg["backend"]),
            cfg.get("model") if isinstance(cfg.get("model"), str) else None,
            dim,
        )
        con.execute("DELETE FROM wiki_embeddings WHERE space_id = ?", (space_id,))
        upsert_wiki_embeddings(con, space_id, zip(vectors.ids, vectors.matrix, strict=True))
    finally:
        con.close()
    return bundle.derived_vectors_path


def _wiki_space_id(cfg: dict) -> str:
    backend = str(cfg.get("backend") or "unknown").replace("/", "_").replace(":", "_")
    model = str(cfg.get("model") or "default").replace("/", "_").replace(":", "_")
    return f"{backend}:{model}"


def embed_committed_page(bundle: Bundle, page) -> bool:
    """Incrementally embed one just-committed page into the same wiki
    embedding space the full rebuild uses, so P5's ``wiki_find(mode="semantic")``
    sees it next round instead of only after the finalize ``wiki rebuild`` (F26).

    Uses the shared passage format and space id, so an incremental vector is
    identical to the one a later full rebuild would produce. Idempotent
    (INSERT OR REPLACE). Returns True if a vector was written.
    """
    from ...embedding import current_backend, embed_passages
    from .graph import wiki_page_passage
    from .store import (
        open_wiki_store,
        upsert_wiki_embedding_space,
        upsert_wiki_embeddings,
    )

    if not page.body_markdown:
        return False
    cfg = current_backend()
    space_id = _wiki_space_id(cfg)
    vec = embed_passages([wiki_page_passage(page)])[0]
    con = open_wiki_store(bundle.sqlite_path)
    try:
        upsert_wiki_embedding_space(
            con,
            space_id,
            str(cfg["backend"]),
            cfg.get("model") if isinstance(cfg.get("model"), str) else None,
            int(vec.shape[0]),
        )
        upsert_wiki_embeddings(con, space_id, [(page.id, vec)])
        con.commit()
    finally:
        con.close()
    return True
