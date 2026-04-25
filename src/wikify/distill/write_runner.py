"""Wiki-side graph + vector rebuild on commit.

After the legacy write pass was retired in the skill-pivot, the only
surviving export from this module is :func:`rebuild_wiki_graph` —
called by ``wikify bundle commit-page`` after each promoted page so the
on-disk wiki graph and per-page embeddings stay in sync.
"""

from __future__ import annotations

from ..models import WikiPage
from ..paths import BundlePaths


def rebuild_wiki_graph(bundle: BundlePaths, pages: list[WikiPage]) -> None:
    """Build and persist the wiki knowledge graph + page vectors."""
    from ..embedding import current_backend, embed_passages, embedder_for
    from ..store.vectors import save_vectors
    from ..store.wiki_graph import (
        build_wiki_graph,
        build_wiki_vectors,
        save_wiki_graph,
    )

    # Build uses passage embedding (indexing wiki page bodies); the graph
    # stores a query-mode callable because search() encodes user queries.
    wiki_vectors = build_wiki_vectors(pages, embed_passages)
    backend = current_backend()
    query_embed = embedder_for(
        str(backend["backend"]), backend.get("model"), mode="query",
    )
    wkg = build_wiki_graph(pages, vectors=wiki_vectors, embed_fn=query_embed)
    save_wiki_graph(bundle.graph_path, wkg)
    if wiki_vectors.ids:
        save_vectors(bundle.wiki_vectors_path, wiki_vectors)
