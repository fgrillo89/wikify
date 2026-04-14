"""Wiki-side knowledge graph: pages, evidence, links, similarity.

Independent from the corpus KnowledgeGraph. Same fluent QueryBuilder pattern,
different node types and traversals. Communicates with the corpus graph via
shared string IDs (chunk_id, doc_id).

The wiki graph rebuilds after every write phase from the current bundle state.
The corpus graph is immutable between ingests. They never merge.

Cross-graph search: the model uses one graph's text as a query into the
other's vector search. The embedder is shared, so the vector spaces are
compatible.

    # Wiki -> Corpus
    page = wkg.page("ALD").first()
    kg.search(page["title"], top_k=10)

    # Corpus -> Wiki
    source = kg.source("paper_A").first()
    wkg.search(source["title"], top_k=5)
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np

if TYPE_CHECKING:
    from ..models import WikiPage
    from .vectors import VectorStore

# ---------------------------------------------------------------------------
# Node type constants
# ---------------------------------------------------------------------------

PAGE = "page"
EVIDENCE = "evidence"

# ---------------------------------------------------------------------------
# Similarity threshold for page-page edges
# ---------------------------------------------------------------------------

WIKI_SIM_COS = 0.45


# ---------------------------------------------------------------------------
# Wiki backend
# ---------------------------------------------------------------------------


@dataclass
class WikiBackend:
    """NetworkX backend for the wiki graph."""

    G: nx.MultiDiGraph

    _links_to: dict[str, set[str]] = field(default_factory=dict)
    _linked_by: dict[str, set[str]] = field(default_factory=dict)
    _co_evidence: dict[str, set[str]] = field(default_factory=dict)
    _evidence_of: dict[str, list[str]] = field(default_factory=dict)

    def rebuild_indexes(self) -> None:
        g = self.G
        self._links_to.clear()
        self._linked_by.clear()
        self._co_evidence.clear()
        self._evidence_of.clear()

        for u, v, data in g.edges(data=True):
            kind = data.get("kind", "")
            if kind == "LINKS_TO":
                self._links_to.setdefault(u, set()).add(v)
                self._linked_by.setdefault(v, set()).add(u)
            elif kind == "CO_EVIDENCE":
                self._co_evidence.setdefault(u, set()).add(v)
                self._co_evidence.setdefault(v, set()).add(u)
            elif kind == "HAS_EVIDENCE":
                self._evidence_of.setdefault(u, []).append(v)

    def node(self, nid: str) -> dict:
        attrs = dict(self.G.nodes[nid])
        attrs["id"] = nid
        return attrs

    def has_node(self, nid: str) -> bool:
        return nid in self.G

    def nodes_of_type(self, ntype: str) -> set[str]:
        return {
            nid for nid, d in self.G.nodes(data=True)
            if d.get("type") == ntype
        }


# ---------------------------------------------------------------------------
# WikiQueryBuilder
# ---------------------------------------------------------------------------


class WikiQueryBuilder:
    """Lazy query builder over the wiki graph. Same pattern as corpus QueryBuilder."""

    __slots__ = ("_wkg", "_ids", "_type")

    def __init__(
        self,
        wkg: WikiKnowledgeGraph,
        node_ids: set[str],
        node_type: str | None = None,
    ) -> None:
        self._wkg = wkg
        self._ids = frozenset(node_ids)
        self._type = node_type

    # ---- Traversal helpers ----

    def _follow(
        self, index: dict, node_type: str, *, exclude_self: bool = False,
    ) -> WikiQueryBuilder:
        """Traverse an inverted index: union all values for current IDs."""
        result: set[str] = set()
        for nid in self._ids:
            hit = index.get(nid)
            if hit is not None:
                result |= hit if isinstance(hit, set) else set(hit)
        if exclude_self:
            result -= self._ids
        return WikiQueryBuilder(self._wkg, result, node_type)

    # ---- Traversals ----

    def links(self) -> WikiQueryBuilder:
        """Pages this page links to."""
        return self._follow(self._wkg._backend._links_to, PAGE)

    def linked_by(self) -> WikiQueryBuilder:
        """Pages that link to this page."""
        return self._follow(self._wkg._backend._linked_by, PAGE)

    def co_evidence(self) -> WikiQueryBuilder:
        """Pages sharing at least one evidence source document."""
        return self._follow(self._wkg._backend._co_evidence, PAGE, exclude_self=True)

    def evidence(self) -> WikiQueryBuilder:
        """Evidence entries for these pages."""
        return self._follow(self._wkg._backend._evidence_of, EVIDENCE)

    # ---- Filters ----

    def where(self, **kwargs: object) -> WikiQueryBuilder:
        backend = self._wkg._backend
        result: set[str] = set()
        for nid in self._ids:
            if not backend.has_node(nid):
                continue
            attrs = backend.G.nodes[nid]
            if all(attrs.get(k) == v for k, v in kwargs.items()):
                result.add(nid)
        return WikiQueryBuilder(self._wkg, result, self._type)

    def top(self, n: int, by: str) -> WikiQueryBuilder:
        backend = self._wkg._backend
        scored: list[tuple[float, str]] = []
        for nid in self._ids:
            if not backend.has_node(nid):
                continue
            val = float(backend.G.nodes[nid].get(by, 0) or 0)
            scored.append((val, nid))
        scored.sort(key=lambda t: -t[0])
        return WikiQueryBuilder(self._wkg, {nid for _, nid in scored[:n]}, self._type)

    # ---- Scoped vector search ----

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Vector search scoped to current page set."""
        vectors = self._wkg._vectors
        embed_fn = self._wkg._embed_fn
        if vectors is None or embed_fn is None:
            return []

        scope_ids = set(self._ids) if self._type == PAGE else set()
        if not scope_ids:
            # If current set is evidence, resolve to their pages
            backend = self._wkg._backend
            for nid in self._ids:
                if backend.has_node(nid):
                    ntype = backend.G.nodes[nid].get("type")
                    if ntype == PAGE:
                        scope_ids.add(nid)
                    elif ntype == EVIDENCE:
                        page_id = backend.G.nodes[nid].get("page_id")
                        if page_id:
                            scope_ids.add(page_id)
        if not scope_ids:
            return []

        vecs = embed_fn([query])
        if vecs.shape[0] == 0:
            return []
        qvec = vecs[0]
        sims = vectors.cosine_to_all(qvec)

        valid_idx: list[int] = []
        for i, pid in enumerate(vectors.ids):
            if pid in scope_ids:
                valid_idx.append(i)
        if not valid_idx:
            return []

        scored = sorted([(sims[i], i) for i in valid_idx], key=lambda t: -t[0])
        backend = self._wkg._backend
        results: list[dict] = []
        for score, idx in scored[:top_k]:
            pid = vectors.ids[idx]
            node_data = backend.node(pid) if backend.has_node(pid) else {"id": pid}
            node_data["score"] = float(score)
            results.append(node_data)
        return results

    # ---- Terminals ----

    def collect(self) -> list[dict]:
        backend = self._wkg._backend
        return [
            backend.node(nid) for nid in sorted(self._ids)
            if backend.has_node(nid)
        ]

    def ids(self) -> list[str]:
        return sorted(self._ids)

    def count(self) -> int:
        return len(self._ids)

    def first(self) -> dict | None:
        if not self._ids:
            return None
        nid = min(self._ids)
        backend = self._wkg._backend
        return backend.node(nid) if backend.has_node(nid) else None

    def exists(self) -> bool:
        return len(self._ids) > 0


# ---------------------------------------------------------------------------
# WikiKnowledgeGraph entry point
# ---------------------------------------------------------------------------


class WikiKnowledgeGraph:
    """Fluent API over wiki pages. Independent from corpus KnowledgeGraph."""

    def __init__(
        self,
        backend: WikiBackend,
        vectors: VectorStore | None = None,
        embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
    ) -> None:
        self._backend = backend
        self._vectors = vectors
        self._embed_fn = embed_fn

    def page(self, page_id: str) -> WikiQueryBuilder:
        ids = {page_id} if self._backend.has_node(page_id) else set()
        return WikiQueryBuilder(self, ids, PAGE)

    def pages(self, **filters: object) -> WikiQueryBuilder:
        ids = self._backend.nodes_of_type(PAGE)
        qb = WikiQueryBuilder(self, ids, PAGE)
        if filters:
            qb = qb.where(**filters)
        return qb

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """Search all pages by vector similarity."""
        return self.pages().search(query, top_k=top_k)

    def stats(self) -> dict:
        n_pages = len(self._backend.nodes_of_type(PAGE))
        n_evidence = len(self._backend.nodes_of_type(EVIDENCE))
        return {
            "pages": n_pages,
            "evidence": n_evidence,
            "edges": self._backend.G.number_of_edges(),
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_wiki_graph(
    pages: list[WikiPage],
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> WikiKnowledgeGraph:
    """Build the wiki knowledge graph from pages."""
    g = nx.MultiDiGraph()

    # ---- Page nodes ----
    for page in pages:
        evidence_doc_ids = sorted({ev.doc_id for ev in page.evidence})
        g.add_node(page.id, **{
            "type": PAGE,
            "title": page.title,
            "kind": page.kind,
            "n_evidence": len(page.evidence),
            "n_links": len(page.links),
            "has_body": bool(page.body_markdown and len(page.body_markdown) > 100),
            "aliases": list(page.aliases),
            "evidence_doc_ids": evidence_doc_ids,
        })

    # ---- Evidence nodes + HAS_EVIDENCE edges ----
    for page in pages:
        for ev in page.evidence:
            ev_id = f"{page.id}::e{ev.marker}"
            g.add_node(ev_id, **{
                "type": EVIDENCE,
                "page_id": page.id,
                "chunk_id": ev.chunk_id,
                "doc_id": ev.doc_id,
                "quote": ev.quote[:200],
            })
            g.add_edge(page.id, ev_id, kind="HAS_EVIDENCE")

    # ---- LINKS_TO edges (from crosslink) ----
    page_ids = {p.id for p in pages}
    for page in pages:
        for target in page.links:
            if target in page_ids and target != page.id:
                g.add_edge(page.id, target, kind="LINKS_TO")

    # ---- CO_EVIDENCE edges (shared doc_id) ----
    doc_to_pages: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        for ev in page.evidence:
            doc_to_pages[ev.doc_id].add(page.id)
    co_ev_seen: set[tuple[str, str]] = set()
    for doc_id, pids in doc_to_pages.items():
        pid_list = sorted(pids)
        for i in range(len(pid_list)):
            for j in range(i + 1, len(pid_list)):
                pair = (pid_list[i], pid_list[j])
                if pair not in co_ev_seen:
                    co_ev_seen.add(pair)
                    g.add_edge(pair[0], pair[1], kind="CO_EVIDENCE")
                    g.add_edge(pair[1], pair[0], kind="CO_EVIDENCE")

    # ---- SIMILAR edges (embedding cosine) ----
    if vectors is not None and vectors.matrix.shape[0] >= 2:
        n = vectors.matrix.shape[0]
        sims = vectors.matrix @ vectors.matrix.T
        np.fill_diagonal(sims, -1.0)
        for i in range(n):
            for j in range(i + 1, n):
                if sims[i, j] >= WIKI_SIM_COS:
                    pid_i = vectors.ids[i]
                    pid_j = vectors.ids[j]
                    if pid_i in page_ids and pid_j in page_ids:
                        g.add_edge(pid_i, pid_j, kind="SIMILAR")
                        g.add_edge(pid_j, pid_i, kind="SIMILAR")

    backend = WikiBackend(G=g)
    backend.rebuild_indexes()
    return WikiKnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)


def build_wiki_vectors(
    pages: list[WikiPage],
    embed_fn: Callable[[Sequence[str]], np.ndarray],
) -> VectorStore:
    """Embed wiki page bodies into a VectorStore.

    Uses title + lead + first 2000 chars of body as the embedding text.
    """
    from .vectors import VectorStore

    ids: list[str] = []
    texts: list[str] = []
    for page in pages:
        if not page.body_markdown:
            continue
        text = f"{page.title}. {page.body_markdown[:2000]}"
        ids.append(page.id)
        texts.append(text)

    if not texts:
        return VectorStore(ids=[], matrix=np.zeros((0, 1), dtype=np.float32))

    matrix = embed_fn(texts)
    # Unit-norm
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms > 0, norms, 1.0)
    return VectorStore(ids=ids, matrix=matrix)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_wiki_graph(path: Path, wkg: WikiKnowledgeGraph) -> None:
    data = nx.node_link_data(wkg._backend.G)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wkg-", suffix=".json", dir=str(path.parent))
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_wiki_graph(
    path: Path,
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> WikiKnowledgeGraph:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    loaded = nx.node_link_graph(data, directed=True, multigraph=True)
    backend = WikiBackend(G=loaded)
    backend.rebuild_indexes()
    return WikiKnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)
