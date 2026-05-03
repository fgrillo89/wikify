"""SQLite-backed adapter for the corpus fluent KnowledgeGraph API.

Exposes the same `_cited_by` / `_authors_of` / `_chunks_of_source` /
`G.nodes[nid]` interface that `corpus/graph.py::QueryBuilder` reads, but
sourced from `wikify.db` instead of a NetworkX MultiDiGraph + JSON.

The backend loads everything into in-memory dicts at construction. For
typical wikify corpora (<= 5k papers, ~150k chunks, ~1M edges) this is
sub-second and matches what the legacy NetworkXBackend did. For larger
corpora it will need lazy loading; see the scaling envelope in
`tasks/sqlite-query-store-plan.md`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from .connection import connect

_NORM_RE = re.compile(r"[^a-z0-9 ]+")

_SECTION_KEYWORDS: list[tuple[str, str]] = [
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


def classify_section(path: list[str]) -> str:
    if not path:
        return "body"
    heading = path[-1].lower().strip()
    heading = re.sub(r"^\d+[\.\)]\s*", "", heading)
    for keyword, stype in _SECTION_KEYWORDS:
        if keyword in heading:
            return stype
    return "body"


def author_key(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"\s+", " ", n).strip().rstrip(",.; ")
    n = re.sub(r"\s+\d+(?:\s*,\s*\d+)*$", "", n)
    key = _NORM_RE.sub(" ", n.lower()).strip()
    return re.sub(r"\s+", " ", key)


# ---------------------------------------------------------------------------
# G shim — minimal NetworkX-like read surface used by QueryBuilder.
# ---------------------------------------------------------------------------


class _NodeView:
    """Mimics nx.Graph.nodes: indexable + callable + iterable."""

    __slots__ = ("_attrs",)

    def __init__(self, attrs: dict[str, dict[str, Any]]):
        self._attrs = attrs

    def __getitem__(self, nid: str) -> dict[str, Any]:
        return self._attrs[nid]

    def __contains__(self, nid: str) -> bool:
        return nid in self._attrs

    def __iter__(self):
        return iter(self._attrs)

    def __len__(self) -> int:
        return len(self._attrs)

    def get(self, nid: str, default: Any = None) -> Any:
        return self._attrs.get(nid, default)

    def __call__(self, *, data: bool = False):
        if data:
            return list(self._attrs.items())
        return list(self._attrs.keys())


class _GShim:
    """Minimal nx.Graph-shaped read surface; does not support edge iteration."""

    __slots__ = ("nodes", "_n_edges")

    def __init__(self, attrs: dict[str, dict[str, Any]], n_edges: int):
        self.nodes = _NodeView(attrs)
        self._n_edges = n_edges

    def __contains__(self, nid: str) -> bool:
        return nid in self.nodes

    def number_of_edges(self) -> int:
        return self._n_edges


# ---------------------------------------------------------------------------
# SqliteGraphBackend
# ---------------------------------------------------------------------------


class SqliteGraphBackend:
    """Drop-in replacement for NetworkXBackend that reads `wikify.db`.

    Populates the inverted-index dicts that `QueryBuilder` consumes;
    exposes `G` as a thin shim so `backend.G.nodes[nid]` keeps working.
    """

    def __init__(self, sqlite_path: str | Path | sqlite3.Connection):
        if isinstance(sqlite_path, sqlite3.Connection):
            self.con = sqlite_path
            self._owns_con = False
        else:
            self.con = connect(sqlite_path)
            self._owns_con = True

        self._node_attrs: dict[str, dict[str, Any]] = {}
        self._cited_by: dict[str, set[str]] = defaultdict(set)
        self._references: dict[str, set[str]] = defaultdict(set)
        self._sources_of: dict[str, set[str]] = defaultdict(set)
        self._authors_of: dict[str, set[str]] = defaultdict(set)
        self._coauthors: dict[str, set[str]] = defaultdict(set)
        self._sections_of: dict[str, list[str]] = defaultdict(list)
        self._chunks_of_source: dict[str, list[str]] = defaultdict(list)
        self._chunks_of_section: dict[str, list[str]] = defaultdict(list)
        self._figures_of: dict[str, list[str]] = defaultdict(list)
        self._equations_of: dict[str, list[str]] = defaultdict(list)
        self._equations_in_chunk: dict[str, list[str]] = defaultdict(list)
        self._figures_near_chunk: dict[str, list[str]] = defaultdict(list)
        self._chunks_near_figure: dict[str, list[str]] = defaultdict(list)
        self._chunks_with_equation: dict[str, list[str]] = defaultdict(list)
        self._pagerank: dict[str, float] = {}
        self._h_index: dict[str, int] = {}
        self._ord_refs: dict[str, dict[int, str]] = {}

        self._load_documents()
        self._load_chunks()
        self._load_authors()
        self._load_assets()
        self._load_bib_cited_only()
        self._load_metrics()
        self._n_edges = self._load_edges()
        self._load_sections_from_chunks()
        self._load_ord_refs()
        self._load_chunk_citation_edges()

        self.G = _GShim(self._node_attrs, self._n_edges)

    # ------------------------------------------------------------------
    # populate
    # ------------------------------------------------------------------

    def _load_documents(self) -> None:
        for r in self.con.execute("SELECT * FROM documents"):
            authors = json.loads(r["authors_json"] or "[]")
            self._node_attrs[r["doc_id"]] = {
                "type": "source",
                "kind": "corpus",
                "title": r["title"] or "",
                "year": r["year"],
                "doi": (r["doi"] or "").lower().strip(),
                "venue": r["container_title"] or "",
                "publisher": r["publisher"] or "",
                "n_chunks": r["n_chunks"] or 0,
                "n_tokens": r["n_tokens"] or 0,
                "authors": authors,
                "markdown_path": r["source_path"] or "",
                "abstract": r["abstract"] or "",
                "tldr": r["tldr"] or "",
            }

    def _load_chunks(self) -> None:
        for r in self.con.execute("SELECT * FROM chunks"):
            section_path = json.loads(r["section_path_json"] or "[]")
            equation_ids = json.loads(r["equation_ids_json"] or "[]")
            self._node_attrs[r["chunk_id"]] = {
                "type": "chunk",
                "source_id": r["doc_id"],
                "doc_id": r["doc_id"],
                "ord": r["ord"],
                "section_type": r["section_type"] or "body",
                "is_boilerplate": bool(r["is_boilerplate"]),
                "char_span": [r["char_start"] or 0, r["char_end"] or 0],
                "equation_ids": equation_ids,
                "section_path": section_path,
                "text": r["text"],
            }

    def _load_authors(self) -> None:
        author_count: dict[str, int] = defaultdict(int)
        for r in self.con.execute("SELECT author_id, doc_id FROM document_authors"):
            author_count[r[0]] += 1
        for r in self.con.execute("SELECT author_id, display_name FROM authors"):
            aid = r["author_id"]
            self._node_attrs[aid] = {
                "type": "author",
                "display_name": r["display_name"] or aid,
                "source_count": author_count.get(aid, 0),
            }

    def _load_assets(self) -> None:
        for r in self.con.execute("SELECT * FROM assets"):
            atype = r["asset_type"] or "image"
            ntype = "figure" if atype in ("figure", "image", "table", "scheme") else "equation"
            attrs = {
                "type": ntype,
                "source_id": r["doc_id"],
                "page": r["page"],
                "path": r["path"] or "",
                "caption": r["caption"] or "",
                "near_chunk_ids": [],
            }
            if ntype == "equation":
                attrs["latex"] = r["content"] or ""
                attrs["label"] = r["caption"] or ""
                meta = json.loads(r["metadata_json"] or "{}")
                attrs["kind"] = meta.get("kind", "math")
                attrs["is_chemical"] = meta.get("kind") == "chemical"
            self._node_attrs[r["asset_id"]] = attrs

    def _load_bib_cited_only(self) -> None:
        """Synthesize cited-only source nodes from unresolved bib_entries.

        These mirror the `kind='cited'` virtual nodes the legacy
        NetworkXBackend produced from citation_index.json.
        """
        for r in self.con.execute(
            "SELECT bib_id, title, authors_json, year, container_title, publisher, doi "
            "FROM bib_entries WHERE target_doc_id IS NULL",
        ):
            if r["bib_id"] in self._node_attrs:
                continue
            authors = json.loads(r["authors_json"] or "[]")
            self._node_attrs[r["bib_id"]] = {
                "type": "source",
                "kind": "cited",
                "title": r["title"] or r["bib_id"],
                "year": r["year"],
                "doi": (r["doi"] or "").lower().strip(),
                "venue": r["container_title"] or "",
                "publisher": r["publisher"] or "",
                "authors": authors,
                "markdown_path": "",
            }

    def _load_metrics(self) -> None:
        for r in self.con.execute(
            "SELECT node_id, value FROM node_metrics "
            "WHERE graph_name='corpus_citation' AND metric='pagerank'",
        ):
            self._pagerank[r[0]] = float(r[1])
            if r[0] in self._node_attrs:
                self._node_attrs[r[0]]["pagerank"] = float(r[1])
        for r in self.con.execute(
            "SELECT node_id, value FROM node_metrics "
            "WHERE node_type='author' AND metric='h_index'",
        ):
            self._h_index[r[0]] = int(r[1])
            if r[0] in self._node_attrs:
                self._node_attrs[r[0]]["h_index"] = int(r[1])
        for r in self.con.execute(
            "SELECT node_id, value FROM node_metrics "
            "WHERE graph_name='corpus_citation' AND metric='citation_count'",
        ):
            if r[0] in self._node_attrs:
                self._node_attrs[r[0]]["citation_count"] = int(r[1])

    def _load_edges(self) -> int:
        n = 0
        for r in self.con.execute(
            "SELECT src_type, src_id, kind, dst_type, dst_id FROM graph_edges",
        ):
            n += 1
            kind = r[2]
            src, dst = r[1], r[4]
            if kind == "references":
                self._references[src].add(dst)
                self._cited_by[dst].add(src)
            elif kind == "authored_by":
                self._authors_of[src].add(dst)
                self._sources_of[dst].add(src)
            elif kind == "coauthor":
                self._coauthors[src].add(dst)
                self._coauthors[dst].add(src)
            elif kind == "has_chunk":
                self._chunks_of_source[src].append(dst)
            elif kind == "has_asset":
                ntype = self._node_attrs.get(dst, {}).get("type")
                if ntype == "figure":
                    self._figures_of[src].append(dst)
                elif ntype == "equation":
                    self._equations_of[src].append(dst)
            elif kind in ("near", "mentions"):
                # chunk -> figure
                if self._node_attrs.get(dst, {}).get("type") == "figure":
                    self._figures_near_chunk[src].append(dst)
                    self._chunks_near_figure[dst].append(src)
                    near_list = self._node_attrs[dst].setdefault("near_chunk_ids", [])
                    if src not in near_list:
                        near_list.append(src)
            elif kind == "contains":
                # chunk -> equation
                if self._node_attrs.get(dst, {}).get("type") == "equation":
                    self._equations_in_chunk[src].append(dst)
                    self._chunks_with_equation[dst].append(src)
        return n

    def _load_sections_from_chunks(self) -> None:
        """Synthesize SECTION nodes from chunks.section_path.

        Section id pattern matches the legacy: `{doc_id}::{path joined}`.
        """
        section_chunks: dict[str, list[tuple[int, str]]] = defaultdict(list)
        section_path_map: dict[str, list[str]] = {}
        for cid, attrs in self._node_attrs.items():
            if attrs.get("type") != "chunk":
                continue
            path = attrs.get("section_path") or []
            if not path:
                continue
            sec_id = f"{attrs['source_id']}::{'/'.join(path)}"
            section_chunks[sec_id].append((attrs.get("ord", 0), cid))
            section_path_map[sec_id] = path

        for sec_id, ord_chunks in section_chunks.items():
            doc_id = sec_id.split("::", 1)[0]
            ord_chunks.sort()
            chunk_ids = [cid for _, cid in ord_chunks]
            path = section_path_map[sec_id]
            self._node_attrs[sec_id] = {
                "type": "section",
                "source_id": doc_id,
                "heading": path[-1] if path else "",
                "level": len(path),
                "section_type": classify_section(path),
                "chunk_ids": chunk_ids,
            }
            self._sections_of[doc_id].append(sec_id)
            self._chunks_of_section[sec_id].extend(chunk_ids)

    def _load_ord_refs(self) -> None:
        """Reconstruct ord_refs[doc_id][ord_n] = target_doc_id from bib_entries.

        ord_n is 1-based to match legacy [N] marker semantics.
        """
        for r in self.con.execute(
            "SELECT doc_id, ord, target_doc_id FROM bib_entries "
            "WHERE target_doc_id IS NOT NULL",
        ):
            doc_id = r["doc_id"]
            self._ord_refs.setdefault(doc_id, {})[int(r["ord"]) + 1] = r["target_doc_id"]
            if doc_id in self._node_attrs:
                self._node_attrs[doc_id]["ord_refs"] = self._ord_refs[doc_id]

    def _load_chunk_citation_edges(self) -> None:
        """Add cites edges to cited-only nodes from chunk_citations rows.

        graph_edges only carries doc->doc references; bib_entry-targeted
        chunk-level citations come from chunk_citations.
        """
        # nothing to do; chunk_citations already mapped chunk→bib_entry,
        # which equals chunk→cited node when bib was unresolved.

    # ------------------------------------------------------------------
    # API used by QueryBuilder
    # ------------------------------------------------------------------

    def node(self, nid: str) -> dict[str, Any]:
        attrs = dict(self._node_attrs.get(nid, {}))
        attrs["id"] = nid
        return attrs

    def has_node(self, nid: str) -> bool:
        return nid in self._node_attrs

    def nodes_of_type(self, ntype: str) -> set[str]:
        return {
            nid for nid, a in self._node_attrs.items() if a.get("type") == ntype
        }

    def neighbors(self, nid: str, hops: int = 1) -> set[str]:
        """Undirected N-hop neighbors. Used by `kg.<...>.neighborhood()`."""
        if nid not in self._node_attrs:
            return set()
        result: set[str] = set()
        frontier = {nid}
        for _ in range(hops):
            next_frontier: set[str] = set()
            for n in frontier:
                for nb in self._undirected_neighbors(n):
                    if nb != nid and nb not in result:
                        next_frontier.add(nb)
            result |= next_frontier
            frontier = next_frontier
        return result

    def _undirected_neighbors(self, nid: str) -> set[str]:
        out: set[str] = set()
        out |= self._references.get(nid, set())
        out |= self._cited_by.get(nid, set())
        out |= self._authors_of.get(nid, set())
        out |= self._sources_of.get(nid, set())
        out |= self._coauthors.get(nid, set())
        out.update(self._chunks_of_source.get(nid, []))
        out.update(self._figures_of.get(nid, []))
        out.update(self._equations_of.get(nid, []))
        out.update(self._figures_near_chunk.get(nid, []))
        out.update(self._chunks_near_figure.get(nid, []))
        out.update(self._equations_in_chunk.get(nid, []))
        out.update(self._chunks_with_equation.get(nid, []))
        out.update(self._sections_of.get(nid, []))
        out.update(self._chunks_of_section.get(nid, []))
        return out

    def close(self) -> None:
        if self._owns_con:
            self.con.close()


def open_corpus_kg(sqlite_path: str | Path) -> SqliteGraphBackend:
    """Open the SQLite corpus KG. Caller owns close()."""
    return SqliteGraphBackend(sqlite_path)
