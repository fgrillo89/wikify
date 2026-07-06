"""Collapse duplicate documents in a built corpus.

The same paper can enter a corpus more than once -- e.g. ingested under a
proper filename and again under a Windows 8.3 short name (``_2013M~2.PDF``),
or under two citekeys. Duplicates fragment a paper's chunks and, worse, split
its incoming citations across several ``doc_id``s so no single id carries the
paper's true centrality. This module groups documents by DOI (then by
normalized title when a DOI is absent), keeps one canonical id per group, and
redirects the duplicates' document-level citation edges onto it before
removing them -- so the canonical inherits the full citation weight.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..api import Corpus
from .store import Store, transaction
from .store.sync import _sync_remove_absent_docs

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _norm_title(title: str | None) -> str:
    return _NON_ALNUM.sub("", (title or "").lower())[:80]


def _canonical(doc_ids: list[str], chunk_counts: dict[str, int]) -> str:
    """Pick the id to keep: prefer a non-mangled name (no ``~``), then the
    richest parse (most chunks), then the shortest / lexically-first id.
    """
    return sorted(
        doc_ids,
        key=lambda d: ("~" in d, -chunk_counts.get(d, 0), len(d), d),
    )[0]


def plan_dedup(store: Store) -> list[dict]:
    """Group documents into duplicate sets. Two documents are duplicates when
    they share a DOI OR a normalized title -- linked transitively, so a paper
    ingested three ways (proper name, second citekey, 8.3 short name) collapses
    into one set even when a stray DOI differs across copies. Returns one entry
    per set with ``>= 2`` members: ``{"key", "canonical", "duplicates"}``.
    """
    rows = list(store.con.execute("SELECT doc_id, doi, title FROM documents"))
    chunk_counts = {
        r[0]: r[1]
        for r in store.con.execute(
            "SELECT doc_id, COUNT(*) FROM chunks GROUP BY doc_id"
        )
    }

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    key_rep: dict[tuple[str, str], str] = {}
    for doc_id, doi, title in rows:
        find(doc_id)  # register
        keys = []
        if doi and doi.strip():
            keys.append(("doi", doi.strip().lower()))
        nt = _norm_title(title)
        if nt:
            keys.append(("title", nt))
        for key in keys:
            if key in key_rep:
                union(doc_id, key_rep[key])
            else:
                key_rep[key] = doc_id

    groups: dict[str, list[str]] = defaultdict(list)
    for doc_id in parent:
        groups[find(doc_id)].append(doc_id)

    plan: list[dict] = []
    for root, doc_ids in groups.items():
        if len(doc_ids) < 2:
            continue
        canonical = _canonical(doc_ids, chunk_counts)
        plan.append({
            "key": root,
            "canonical": canonical,
            "duplicates": sorted(d for d in doc_ids if d != canonical),
        })
    return plan


def apply_dedup(corpus: Corpus) -> dict:
    """Execute the plan: redirect citation edges, then drop duplicate
    documents (and their chunks / embeddings / orphaned authors via the
    store's cascade-correct removal) and rebuild the FTS index. Returns the
    plan and the sorted list of removed ids.
    """
    store = Store(corpus.sqlite_path)
    try:
        removed: set[str] = set()
        with transaction(store.con):
            plan = plan_dedup(store)
            for group in plan:
                canonical = group["canonical"]
                for dup in group["duplicates"]:
                    # Repoint the duplicate's document-level edges (citations
                    # in both directions) onto the canonical id. UPDATE OR
                    # REPLACE resolves the primary-key clash when the canonical
                    # already carries the same edge.
                    store.con.execute(
                        "UPDATE OR REPLACE graph_edges SET src_id=? "
                        "WHERE src_type='document' AND src_id=?",
                        (canonical, dup),
                    )
                    store.con.execute(
                        "UPDATE OR REPLACE graph_edges SET dst_id=? "
                        "WHERE dst_type='document' AND dst_id=?",
                        (canonical, dup),
                    )
                    removed.add(dup)
            # Drop the self-citations the redirect can create.
            store.con.execute(
                "DELETE FROM graph_edges WHERE src_type='document' "
                "AND dst_type='document' AND src_id=dst_id"
            )
            keep = {
                r[0] for r in store.con.execute("SELECT doc_id FROM documents")
            } - removed
            _sync_remove_absent_docs(store, keep)
        store.fts_rebuild()
    finally:
        store.close()
    return {"groups": plan, "removed": sorted(removed)}
