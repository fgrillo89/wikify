"""Build the KnowledgeGraph from ingest data.

Called as Wave D in the ingest pipeline, after citation edges are resolved.
Creates all node types (Source, Author, Chunk, Section, Figure, Equation),
all edge types, computes PageRank/h-index/communities, builds inverted
indexes, and persists to knowledge_graph.json.

The builder reads Document, Chunk, VectorStore, and citation_index.
It never calls external APIs or models -- pure computation on local data.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..models import Chunk, Document
from .graph import AUTHOR, CHUNK, EQUATION, FIGURE, SECTION, SOURCE, KnowledgeGraph, NetworkXBackend

if TYPE_CHECKING:
    from ..store.vectors import VectorStore

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _author_key(name: str) -> str:
    """Normalize author name to a stable key matching distill/author_context.py.

    'Smith, J.' -> 'smith j', preserving the same keying as AuthorContext.
    """
    import unicodedata

    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\s+", " ", n).strip().rstrip(",.; ")
    n = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", n)
    key = _NORM_RE.sub(" ", n.lower()).strip()
    return re.sub(r"\s+", " ", key)


def build_knowledge_graph(
    docs: list[Document],
    chunks: list[Chunk],
    vectors: VectorStore | None = None,
    citation_index: dict | None = None,
) -> KnowledgeGraph:
    """Construct the full knowledge graph from ingest data.

    Returns a KnowledgeGraph with NetworkXBackend. The VectorStore is
    attached for search() but not embedded in the graph -- it stays
    external, bridged by shared chunk IDs.
    """
    g = nx.MultiDiGraph()
    chunks_by_doc: dict[str, list[Chunk]] = defaultdict(list)
    for ck in chunks:
        chunks_by_doc[ck.doc_id].append(ck)

    # ------------------------------------------------------------------
    # 1. Source nodes (corpus docs + cited works)
    # ------------------------------------------------------------------

    # Pre-build DOI/title indexes once for ord_refs resolution (O(D) not O(D^2))
    doi_to_id: dict[str, str] = {}
    title_to_id: dict[str, str] = {}
    for d in docs:
        doi = (d.metadata.get("doi") or "").lower().strip()
        if doi:
            doi_to_id[doi] = d.id
        if d.title and len(d.title) > 15:
            title_to_id[d.title.lower()[:50]] = d.id

    for doc in docs:
        meta = doc.metadata
        authors_raw = meta.get("authors") or []
        g.add_node(doc.id, **{
            "type": SOURCE,
            "title": doc.title,
            "year": meta.get("year"),
            "doi": (meta.get("doi") or "").lower().strip(),
            "venue": meta.get("venue", ""),
            "authors": authors_raw,
            "kind": "corpus",
            "markdown_path": doc.markdown_path,
            "n_chunks": doc.n_chunks,
            "n_tokens": doc.n_tokens,
        })

        # ord_refs: [N] -> target source_id (from citation entries)
        ord_refs: dict[int, str] = {}
        if doc.citations and doc.cites:
            for cit in doc.citations:
                target = None
                if cit.doi:
                    target = doi_to_id.get(cit.doi.lower().strip())
                if not target and cit.title and len(cit.title) > 15:
                    target = title_to_id.get(cit.title.lower()[:50])
                if target:
                    ord_refs[cit.ord] = target
        if ord_refs:
            g.nodes[doc.id]["ord_refs"] = ord_refs

    # Cited-only source nodes (referenced but not in corpus)
    _cited_entries = _extract_cited_works(docs, citation_index)
    for cid, attrs in _cited_entries.items():
        if cid not in g:
            g.add_node(cid, type=SOURCE, kind="cited", **attrs)

    # ------------------------------------------------------------------
    # 2. Author nodes
    # ------------------------------------------------------------------
    author_sources: dict[str, set[str]] = defaultdict(set)
    author_display: dict[str, str] = {}
    for doc in docs:
        authors_raw = doc.metadata.get("authors") or []
        for name in authors_raw:
            key = _author_key(name)
            if not key:
                continue
            author_sources[key].add(doc.id)
            if key not in author_display:
                author_display[key] = name

    for key, src_ids in author_sources.items():
        g.add_node(key, **{
            "type": AUTHOR,
            "display_name": author_display.get(key, key),
            "source_count": len(src_ids),
        })

    # ------------------------------------------------------------------
    # 3. Chunk nodes
    # ------------------------------------------------------------------
    for ck in chunks:
        g.add_node(ck.id, **{
            "type": CHUNK,
            "source_id": ck.doc_id,
            "ord": ck.ord,
            "section_type": ck.section_type,
            "char_span": list(ck.char_span),
            "equation_ids": list(ck.equation_ids),
        })

    # ------------------------------------------------------------------
    # 4. Section nodes
    # ------------------------------------------------------------------
    for doc in docs:
        for sec in doc.sections:
            sec_id = f"{doc.id}::{'/'.join(sec.path)}"
            g.add_node(sec_id, **{
                "type": SECTION,
                "source_id": doc.id,
                "heading": sec.path[-1] if sec.path else "",
                "level": len(sec.path),
                "section_type": _classify_section(sec.path),
                "chunk_ids": list(sec.chunk_ids),
            })
            # CONTAINS_SECTION
            g.add_edge(doc.id, sec_id, kind="CONTAINS_SECTION")
            # CHUNK_IN_SECTION
            for cid in sec.chunk_ids:
                if cid in g:
                    g.add_edge(cid, sec_id, kind="CHUNK_IN_SECTION")

    # ------------------------------------------------------------------
    # 5. Figure nodes
    # ------------------------------------------------------------------
    for doc in docs:
        for img in doc.images:
            g.add_node(img.id, **{
                "type": FIGURE,
                "source_id": doc.id,
                "caption": img.caption,
                "path": img.path,
                "page": img.page,
                "near_chunk_ids": list(img.near_chunk_ids),
            })
            g.add_edge(doc.id, img.id, kind="CONTAINS_FIGURE")
            for cid in img.near_chunk_ids:
                if cid in g:
                    g.add_edge(img.id, cid, kind="FIGURE_NEAR_CHUNK")

    # ------------------------------------------------------------------
    # 6. Equation nodes + equation-chunk edges
    # ------------------------------------------------------------------
    for doc in docs:
        for eq in doc.equations:
            eq_id = eq.get("id", "")
            if not eq_id:
                continue
            eq_type = eq.get("type", "")
            g.add_node(eq_id, **{
                "type": EQUATION,
                "source_id": doc.id,
                "latex": eq.get("latex", ""),
                "label": eq.get("label", ""),
                "kind": eq_type,
                "is_chemical": eq_type == "chemical",
            })
            g.add_edge(doc.id, eq_id, kind="CONTAINS_EQUATION")

    # Build EQUATION_IN_CHUNK edges from chunk.equation_ids
    for ck in chunks:
        for eq_id in ck.equation_ids:
            if eq_id in g:
                g.add_edge(eq_id, ck.id, kind="EQUATION_IN_CHUNK")

    # ------------------------------------------------------------------
    # 7. Citation edges (CITES)
    # ------------------------------------------------------------------
    for doc in docs:
        for target_id in doc.cites or []:
            if target_id in g:
                g.add_edge(doc.id, target_id, kind="CITES")

    # ------------------------------------------------------------------
    # 8. Authorship edges (AUTHORED_BY + COLLABORATED)
    # ------------------------------------------------------------------
    for doc in docs:
        authors_raw = doc.metadata.get("authors") or []
        keys = [_author_key(name) for name in authors_raw]
        keys = [k for k in keys if k]
        for i, key in enumerate(keys):
            g.add_edge(doc.id, key, kind="AUTHORED_BY", position=i)
        # Co-authorship: pairwise within same paper
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                if not g.has_edge(keys[i], keys[j]):
                    g.add_edge(keys[i], keys[j], kind="COLLABORATED")
                    g.add_edge(keys[j], keys[i], kind="COLLABORATED")

    # ------------------------------------------------------------------
    # 9. CONTAINS_CHUNK edges
    # ------------------------------------------------------------------
    for ck in chunks:
        g.add_edge(ck.doc_id, ck.id, kind="CONTAINS_CHUNK")

    # ------------------------------------------------------------------
    # 10. Metrics: PageRank, h-index, citation counts
    # ------------------------------------------------------------------
    _compute_metrics(g, author_sources)

    # ------------------------------------------------------------------
    # 11. Build backend + indexes
    # ------------------------------------------------------------------
    backend = NetworkXBackend(G=g)
    backend.rebuild_indexes()

    return KnowledgeGraph(backend=backend, vectors=vectors)


def _extract_cited_works(
    docs: list[Document],
    citation_index: dict | None,
) -> dict[str, dict]:
    """Extract cited-only source nodes from citation entries.

    These are works referenced by corpus papers but not in the corpus
    themselves. We create lightweight source nodes for them so the graph
    can represent the full citation neighborhood.
    """
    corpus_ids = {d.id for d in docs}
    cited: dict[str, dict] = {}

    if citation_index:
        entries = citation_index.get("entries", {})
        for key, entry in entries.items():
            if key not in corpus_ids:
                cited[key] = {
                    "title": entry.get("title", ""),
                    "year": entry.get("year"),
                    "doi": entry.get("doi", ""),
                    "authors": entry.get("authors", []),
                }

    return cited


def _classify_section(path: list[str]) -> str:
    """Classify section by its heading path into a canonical type."""
    if not path:
        return "body"
    heading = path[-1].lower().strip()
    heading = re.sub(r"^\d+[\.\)]\s*", "", heading)
    for keyword, stype in _SECTION_KEYWORDS:
        if keyword in heading:
            return stype
    return "body"


_SECTION_KEYWORDS = [
    ("abstract", "abstract"),
    ("introduction", "introduction"),
    ("background", "introduction"),
    ("method", "methods"),
    ("experimental", "methods"),
    ("materials", "methods"),
    ("result", "results"),
    ("discussion", "discussion"),
    ("conclusion", "conclusions"),
    ("summary", "conclusions"),
    ("reference", "references"),
    ("bibliography", "references"),
    ("acknowledgment", "acknowledgments"),
    ("acknowledgement", "acknowledgments"),
    ("supplement", "supplementary"),
    ("appendix", "supplementary"),
    ("supporting info", "supplementary"),
]


def _compute_metrics(
    graph: nx.MultiDiGraph,
    author_sources: dict[str, set[str]],
) -> None:
    """Compute PageRank, citation counts, h-index on the graph."""
    # Build citation-only subgraph for PageRank
    cite_edges = [
        (u, v) for u, v, d in graph.edges(data=True)
        if d.get("kind") == "CITES"
    ]
    if cite_edges:
        cite_g = nx.DiGraph(cite_edges)
        pr = nx.pagerank(cite_g, alpha=0.85)
        for nid, score in pr.items():
            if nid in graph:
                graph.nodes[nid]["pagerank"] = round(score, 8)

    # Citation count per source
    for nid, ndata in graph.nodes(data=True):
        if ndata.get("type") == SOURCE:
            count = sum(
                1 for _, _, d in graph.in_edges(nid, data=True)
                if d.get("kind") == "CITES"
            )
            graph.nodes[nid]["citation_count"] = count

    # h-index per author
    for author_key, src_ids in author_sources.items():
        counts = sorted(
            (graph.nodes[sid].get("citation_count", 0) for sid in src_ids if sid in graph),
            reverse=True,
        )
        h = 0
        for i, c in enumerate(counts, 1):
            if c >= i:
                h = i
            else:
                break
        if author_key in graph:
            graph.nodes[author_key]["h_index"] = h
            graph.nodes[author_key]["citation_count"] = sum(counts)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_knowledge_graph(path: Path, kg: KnowledgeGraph) -> None:
    """Persist the knowledge graph to JSON via nx.node_link_data."""
    data = nx.node_link_data(kg._backend.G)
    path.parent.mkdir(parents=True, exist_ok=True)

    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(
        prefix=".kg-", suffix=".json", dir=str(path.parent),
    )
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_knowledge_graph(
    path: Path,
    vectors: VectorStore | None = None,
    embed_fn: object | None = None,
) -> KnowledgeGraph:
    """Load a persisted knowledge graph from JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    loaded = nx.node_link_graph(data, directed=True, multigraph=True)
    backend = NetworkXBackend(G=loaded)
    backend.rebuild_indexes()
    return KnowledgeGraph(backend=backend, vectors=vectors, embed_fn=embed_fn)
