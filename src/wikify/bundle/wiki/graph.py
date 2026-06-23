"""Wiki-side knowledge graph: pages, evidence, links, similarity.

Backed by `<bundle_root>/wiki.db`. Same fluent QueryBuilder pattern as
the corpus side; cross-graph search uses one graph's text as input to
the other's vector search via a shared embedder.

    # Wiki -> Corpus
    page = wkg.page("ALD").first()
    kg.search(page["title"], top_k=10)

    # Corpus -> Wiki
    source = kg.source("paper_A").first()
    wkg.search(source["title"], top_k=5)
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ...corpus.store.kg import _GShim
from .store import open_wiki_store, upsert_wiki_page

if TYPE_CHECKING:
    from ...corpus.vectors import VectorStore
    from ...models import WikiPage


# ---------------------------------------------------------------------------
# Node type constants
# ---------------------------------------------------------------------------

PAGE = "page"
EVIDENCE = "evidence"

# Threshold above which two pages are similar enough to add a SIMILAR edge.
WIKI_SIM_COS = 0.45


# ---------------------------------------------------------------------------
# SQLite-backed wiki backend
# ---------------------------------------------------------------------------


class WikiBackend:
    """SQLite-backed wiki graph backend.

    Drop-in for the previous nx.MultiDiGraph backend: exposes the same
    `_links_to`, `_linked_by`, `_co_evidence`, `_evidence_of` dicts and a
    `G.nodes[nid]` shim. All data comes from `wiki.db` rows.
    """

    def __init__(self, sqlite_path: str | Path | sqlite3.Connection):
        if isinstance(sqlite_path, sqlite3.Connection):
            self.con = sqlite_path
            self._owns_con = False
        else:
            self.con = open_wiki_store(sqlite_path)
            self._owns_con = True

        self._node_attrs: dict[str, dict] = {}
        self._links_to: dict[str, set[str]] = defaultdict(set)
        self._linked_by: dict[str, set[str]] = defaultdict(set)
        self._co_evidence: dict[str, set[str]] = defaultdict(set)
        self._evidence_of: dict[str, list[str]] = defaultdict(list)

        self._load_pages()
        self._load_evidence()
        n_edges = self._load_edges()
        self.G = _GShim(self._node_attrs, n_edges)

    def _load_pages(self) -> None:
        for r in self.con.execute("SELECT * FROM wiki_pages"):
            fm = json.loads(r["frontmatter_json"] or "{}")
            self._node_attrs[r["page_id"]] = {
                "type": PAGE,
                "title": r["title"] or r["page_id"],
                "kind": r["kind"] or "article",
                "slug": r["slug"],
                "n_evidence": 0,  # filled in _load_evidence
                "n_links": 0,
                "has_body": bool(r["body"] and len(r["body"]) > 100),
                "aliases": list(fm.get("aliases") or []),
                "evidence_doc_ids": [],
            }

    def _load_evidence(self) -> None:
        per_page: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for r in self.con.execute(
            "SELECT page_id, marker, chunk_id, doc_id, quote FROM wiki_evidence "
            "ORDER BY page_id, marker",
        ):
            per_page[r["page_id"]].append(
                (r["marker"], r["chunk_id"] or "", r["doc_id"] or "", r["quote"] or ""),
            )
        for page_id, items in per_page.items():
            doc_ids = sorted({d for _, _, d, _ in items if d})
            attrs = self._node_attrs.setdefault(page_id, {
                "type": PAGE, "title": page_id, "kind": "article",
                "aliases": [], "evidence_doc_ids": [],
            })
            attrs["n_evidence"] = len(items)
            attrs["evidence_doc_ids"] = doc_ids
            for marker, chunk_id, doc_id, quote in items:
                marker_part = marker if marker.startswith("e") else f"e{marker}"
                ev_id = f"{page_id}::{marker_part}"
                self._node_attrs[ev_id] = {
                    "type": EVIDENCE,
                    "page_id": page_id,
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "quote": quote[:200],
                    "marker": marker,
                }
                self._evidence_of[page_id].append(ev_id)

    def _load_edges(self) -> int:
        n = 0
        for r in self.con.execute("SELECT src_id, kind, dst_type, dst_id FROM wiki_edges"):
            n += 1
            kind = r["kind"]
            if kind == "links_to":
                self._links_to[r["src_id"]].add(r["dst_id"])
                self._linked_by[r["dst_id"]].add(r["src_id"])
                if r["src_id"] in self._node_attrs:
                    self._node_attrs[r["src_id"]]["n_links"] = (
                        self._node_attrs[r["src_id"]].get("n_links", 0) + 1
                    )
        # CO_EVIDENCE: pages that share at least one doc_id via evidence.
        doc_to_pages: dict[str, set[str]] = defaultdict(set)
        for r in self.con.execute(
            "SELECT page_id, doc_id FROM wiki_evidence WHERE doc_id IS NOT NULL",
        ):
            doc_to_pages[r["doc_id"]].add(r["page_id"])
        for pids in doc_to_pages.values():
            pid_list = sorted(pids)
            for i in range(len(pid_list)):
                for j in range(i + 1, len(pid_list)):
                    self._co_evidence[pid_list[i]].add(pid_list[j])
                    self._co_evidence[pid_list[j]].add(pid_list[i])
                    n += 2  # virtual edges (not in wiki_edges, but counted)
        return n

    # ------------------------------------------------------------------
    # API used by WikiQueryBuilder
    # ------------------------------------------------------------------

    def node(self, nid: str) -> dict:
        attrs = dict(self._node_attrs.get(nid, {}))
        attrs["id"] = nid
        return attrs

    def has_node(self, nid: str) -> bool:
        return nid in self._node_attrs

    def nodes_of_type(self, ntype: str) -> set[str]:
        return {nid for nid, a in self._node_attrs.items() if a.get("type") == ntype}

    def close(self) -> None:
        if self._owns_con:
            self.con.close()


# ---------------------------------------------------------------------------
# WikiQueryBuilder
# ---------------------------------------------------------------------------


class WikiQueryBuilder:
    """Lazy query builder over the wiki graph."""

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

    def _follow(
        self, index: dict, node_type: str, *, exclude_self: bool = False,
    ) -> WikiQueryBuilder:
        result: set[str] = set()
        for nid in self._ids:
            hit = index.get(nid)
            if hit is not None:
                result |= hit if isinstance(hit, set) else set(hit)
        if exclude_self:
            result -= self._ids
        return WikiQueryBuilder(self._wkg, result, node_type)

    def links(self) -> WikiQueryBuilder:
        return self._follow(self._wkg._backend._links_to, PAGE)

    def linked_by(self) -> WikiQueryBuilder:
        return self._follow(self._wkg._backend._linked_by, PAGE)

    def co_evidence(self) -> WikiQueryBuilder:
        return self._follow(self._wkg._backend._co_evidence, PAGE, exclude_self=True)

    def evidence(self) -> WikiQueryBuilder:
        return self._follow(self._wkg._backend._evidence_of, EVIDENCE)

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

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        vectors = self._wkg._vectors
        embed_fn = self._wkg._embed_fn
        if vectors is None or embed_fn is None:
            return []

        scope_ids = set(self._ids) if self._type == PAGE else set()
        if not scope_ids:
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

        valid_idx: list[int] = [
            i for i, pid in enumerate(vectors.ids) if pid in scope_ids
        ]
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

    def titles(self) -> list[str]:
        backend = self._wkg._backend
        out: list[str] = []
        for nid in sorted(self._ids):
            if not backend.has_node(nid):
                continue
            attrs = backend.G.nodes[nid]
            out.append(str(attrs.get("title", nid)))
        return out


# ---------------------------------------------------------------------------
# WikiKnowledgeGraph entry point
# ---------------------------------------------------------------------------


class WikiKnowledgeGraph:
    """Fluent API over wiki pages."""

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
# Builders + persistence
# ---------------------------------------------------------------------------


def build_wiki_graph(
    pages: list[WikiPage],
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> WikiKnowledgeGraph:
    """Build a wiki KG over an in-memory page list (no SQLite required).

    Used by tools that hand over a list of WikiPage without a bundle —
    primarily smoke tests and CLI dry-runs. The returned KG is backed
    by a :memory: SQLite store populated from *pages*.
    """
    from .store import open_wiki_store

    con = open_wiki_store(":memory:")
    for page in pages:
        upsert_wiki_page(
            con,
            page_id=page.id,
            slug=getattr(page, "slug", page.id),
            title=page.title or page.id,
            kind=page.kind,
            body=page.body_markdown or "",
            frontmatter={"aliases": list(page.aliases or [])},
            evidence=[
                {"marker": ev.marker, "chunk_id": ev.chunk_id,
                 "doc_id": ev.doc_id, "quote": ev.quote}
                for ev in (page.evidence or [])
            ],
            links=[link for link in (page.links or []) if link != page.id],
        )
    backend = WikiBackend(con)
    return WikiKnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)


def wiki_page_passage(page: WikiPage) -> str:
    """Passage text embedded for a wiki page. Shared by the full rebuild and
    the incremental commit-time embedding so the two stay byte-identical."""
    return f"{page.title}. {page.body_markdown[:2000]}"


def build_wiki_vectors(
    pages: list[WikiPage],
    embed_fn: Callable[[Sequence[str]], np.ndarray],
) -> VectorStore:
    """Embed wiki page bodies into a VectorStore."""
    from ...corpus.vectors import VectorStore

    ids: list[str] = []
    texts: list[str] = []
    for page in pages:
        if not page.body_markdown:
            continue
        ids.append(page.id)
        texts.append(wiki_page_passage(page))

    if not texts:
        return VectorStore(ids=[], matrix=np.zeros((0, 1), dtype=np.float32))

    matrix = embed_fn(texts)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms > 0, norms, 1.0)
    return VectorStore(ids=ids, matrix=matrix.astype(np.float32))


def open_wiki_kg(
    sqlite_path: str | Path,
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> WikiKnowledgeGraph:
    """Open the wiki KG at *sqlite_path* (caller doesn't need to read SQL)."""
    backend = WikiBackend(sqlite_path)
    return WikiKnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)


# ---------------------------------------------------------------------------
# Backwards-compatible legacy wrappers (no-op SQLite paths).
# ---------------------------------------------------------------------------


def save_wiki_graph(path: Path, wkg: WikiKnowledgeGraph) -> None:
    """Persist *wkg* to a fresh SQLite database at *path*.

    The wiki graph IS wiki.db; this function copies the in-memory
    backend's contents into a new file. Used by tests that round-trip
    a built graph; production code mutates `<bundle_root>/wiki.db`
    directly via :func:`upsert_wiki_page`.
    """
    from .store import open_wiki_store

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    target = open_wiki_store(p)
    try:
        for page_id, attrs in wkg._backend._node_attrs.items():
            if attrs.get("type") != PAGE:
                continue
            evidence_rows = []
            for ev_id in wkg._backend._evidence_of.get(page_id, []):
                ev_attrs = wkg._backend._node_attrs.get(ev_id, {})
                evidence_rows.append({
                    "marker": ev_attrs.get("marker", ev_id.split("::")[-1]),
                    "chunk_id": ev_attrs.get("chunk_id", ""),
                    "doc_id": ev_attrs.get("doc_id", ""),
                    "quote": ev_attrs.get("quote", ""),
                })
            upsert_wiki_page(
                target,
                page_id=page_id,
                slug=attrs.get("slug") or page_id,
                title=attrs.get("title", page_id),
                kind=attrs.get("kind", "article"),
                body=attrs.get("body", ""),
                frontmatter={"aliases": list(attrs.get("aliases") or [])},
                evidence=evidence_rows,
                links=list(wkg._backend._links_to.get(page_id, set())),
            )
    finally:
        target.close()


def load_wiki_graph(
    path: Path,
    vectors: VectorStore | None = None,
    embed_fn: Callable[[Sequence[str]], np.ndarray] | None = None,
) -> WikiKnowledgeGraph:
    """Open the wiki KG. *path* is the SQLite file path."""
    backend = WikiBackend(Path(path))
    return WikiKnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)
