"""``derived/`` projections — rebuildable machine views of the committed wiki.

Three projections:

- ``derived/index.json``    list of every committed page (slug + path + kind).
- ``derived/graph.json``    serialised wiki knowledge graph (cite edges
                             between pages). Rebuilt via the existing
                             ``bundle/wiki/graph.py`` helpers.
- ``derived/vectors.npz``   per-page embeddings, used for ``wiki find``.

The graph + vectors rebuild reads every ``wiki/articles/*.md`` and
``wiki/people/*.md`` and reconstructs the graph from the
``[^eN]`` evidence footnotes in each page body. The
``bundle/wiki/graph.py`` helpers do the heavy lifting; this module
adapts them to the bundle's ``derived_*`` paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...api import Bundle


def list_committed_pages(bundle: Bundle) -> list[dict]:
    """Walk wiki/articles/ + wiki/people/ and return per-page metadata."""
    out: list[dict] = []
    for kind, sub in (("article", bundle.wiki_articles_dir), ("person", bundle.wiki_people_dir)):
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
    """Walk wiki/articles/ + wiki/people/ and return parsed WikiPages."""
    from .page import parse_page

    out: list = []
    for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
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
    """Refresh `wiki.db` rows from every committed page on disk.

    Walks `wiki/articles/` + `wiki/people/`, parses each markdown file,
    and upserts the result into `wiki.db`. The wiki graph IS wiki.db;
    `derived/graph.json` is no longer produced.
    """
    from .page import parse_page
    from .store import open_wiki_store, upsert_wiki_page

    con = open_wiki_store(bundle.sqlite_path)
    try:
        for sub in (bundle.wiki_articles_dir, bundle.wiki_people_dir):
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
    return bundle.sqlite_path


def rebuild_vectors(bundle: Bundle) -> Path:
    """Rebuild ``derived/vectors.npz`` — per-page embeddings.

    Uses the project's current embedding backend in passage mode.
    The vectors file is the input the ``wiki find`` semantic-search
    path will consume.
    """
    from ...corpus.vectors import save_vectors
    from ...embedding import embed_passages
    from .graph import build_wiki_vectors

    bundle.derived_dir.mkdir(parents=True, exist_ok=True)
    pages = _load_pages(bundle)
    if not pages:
        # No committed pages — write an empty vectors file so callers can
        # still ``np.load`` without branching.
        import numpy as _np

        from ...corpus.vectors import VectorStore

        empty = VectorStore(matrix=_np.zeros((0, 0), dtype="float32"), ids=[])
        save_vectors(bundle.derived_vectors_path, empty)
        return bundle.derived_vectors_path
    vectors = build_wiki_vectors(pages, embed_passages)
    save_vectors(bundle.derived_vectors_path, vectors)
    return bundle.derived_vectors_path
