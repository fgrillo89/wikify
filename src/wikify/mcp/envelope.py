"""Lightweight envelope and item builders for MCP tool responses.

The envelope keeps a stable shape across every tool::

    {"ok": True, "kind": <str>, "items": [...], "notes": [...], "next": null}

Errors share the envelope::

    {"ok": False, "code": <stable code>, "message": <human text>,
     "details": {...} | None}

Item builders produce per-row dicts with common fields (``handle``,
``type``, ``title``, ``score``, ``rank``, ``resource_uri``, ``preview``,
``meta``). Shape-specific data lives under ``meta`` so the schema
stays loose.
"""

from __future__ import annotations

from typing import Any

from ..corpus.handles import format_handle, short_id
from ..models import Chunk, Document

_PREVIEW_CHARS = 240


def ok(kind: str, *, items: list[dict] | None = None,
       notes: list[str] | None = None,
       next_: str | None = None) -> dict:
    """Build a success envelope."""
    return {
        "ok": True,
        "kind": kind,
        "items": items or [],
        "notes": notes or [],
        "next": next_,
    }


def err(code: str, message: str, **details: Any) -> dict:
    """Build an error envelope."""
    return {
        "ok": False,
        "code": code,
        "message": message,
        "details": details or None,
    }


# -------------------------------------------------------------- URI helpers


def _author_uri_ident(key: str) -> str:
    """URIs disallow spaces. Author keys are lowercase ``"first last"``."""
    return key.replace(" ", "_")


def doc_uri(doc_id: str) -> str:
    return f"wikify://corpus/docs/{short_id(doc_id)}"


def chunk_uri(chunk_id: str) -> str:
    return f"wikify://corpus/chunks/{short_id(chunk_id)}"


def figure_uri(fig_id: str) -> str:
    """Two-segment URI: ``figures/{doc_short}/{stem}``.

    FastMCP resource templates use ``[^/]+`` per parameter, so embedded
    slashes (figure ids are ``<doc_short>/<stem>``) need separate
    segments. ``short_id`` keeps the slash structure for figures.
    """
    return f"wikify://corpus/figures/{short_id(fig_id)}"


def equation_uri(eq_id: str) -> str:
    return f"wikify://corpus/equations/{short_id(eq_id)}"


def author_uri(key: str) -> str:
    return f"wikify://corpus/authors/{_author_uri_ident(key)}"


# ----------------------------------------------------------- item builders


def doc_item(doc: Document, *,
             score: float | None = None,
             rank: dict | None = None,
             best_chunk_id: str | None = None,
             n_match_chunks: int | None = None) -> dict:
    """Build an envelope item for a :class:`Document`."""
    meta: dict[str, Any] = {
        "kind": doc.kind,
        "n_chunks": doc.n_chunks,
    }
    if best_chunk_id:
        meta["best_chunk_handle"] = format_handle("chunk", best_chunk_id)
    if n_match_chunks is not None:
        meta["n_match_chunks"] = n_match_chunks
    if doc.metadata:
        if "year" in doc.metadata:
            meta["year"] = doc.metadata["year"]
        if "authors" in doc.metadata:
            meta["n_authors"] = len(doc.metadata.get("authors") or [])
    return {
        "handle": format_handle("doc", doc.id),
        "type": "doc",
        "title": doc.title or "",
        "score": score,
        "rank": rank,
        "resource_uri": doc_uri(doc.id),
        "preview": (doc.title or "")[:_PREVIEW_CHARS],
        "meta": meta,
    }


def doc_row_item(row: dict, *, score: float | None = None) -> dict:
    """Build an item from a ``rank_docs`` / ``search_papers`` row dict."""
    doc_id = str(row.get("doc_id", ""))
    rank: dict[str, Any] = {}
    if "citation_count" in row:
        rank["citation_count"] = int(row.get("citation_count", 0) or 0)
    if "pagerank" in row:
        rank["pagerank"] = float(row.get("pagerank", 0.0) or 0.0)
    meta: dict[str, Any] = {}
    if "n_chunks" in row:
        meta["n_match_chunks"] = int(row.get("n_chunks", 0) or 0)
    if "best_chunk_id" in row and row["best_chunk_id"]:
        meta["best_chunk_handle"] = format_handle(
            "chunk", str(row["best_chunk_id"])
        )
    if "chunk_ids" in row:
        meta["matched_chunk_handles"] = [
            format_handle("chunk", str(cid)) for cid in row.get("chunk_ids", [])
        ]
    if score is None and "best_score" in row:
        score = float(row["best_score"])
    return {
        "handle": format_handle("doc", doc_id),
        "type": "doc",
        "title": str(row.get("title", "") or ""),
        "score": score,
        "rank": rank or None,
        "resource_uri": doc_uri(doc_id),
        "preview": str(row.get("title", "") or "")[:_PREVIEW_CHARS],
        "meta": meta or None,
    }


def chunk_item(chunk: Chunk, *,
               score: float | None = None,
               full: bool = False) -> dict:
    """Build an envelope item for a :class:`Chunk`."""
    text = chunk.text if full else chunk.text[:_PREVIEW_CHARS]
    item = {
        "handle": format_handle("chunk", chunk.id),
        "type": "chunk",
        "title": "",
        "score": score,
        "rank": None,
        "resource_uri": chunk_uri(chunk.id),
        "preview": chunk.text[:_PREVIEW_CHARS],
        "meta": {
            "doc_handle": format_handle("doc", chunk.doc_id),
            "section_path": list(chunk.section_path or []),
        },
    }
    if full:
        item["text"] = text
    return item


def chunk_row_item(row: dict, *, score: float | None = None) -> dict:
    """Build a chunk item from a search-result dict."""
    chunk_id = str(row.get("id", ""))
    doc_id = str(row.get("doc_id") or row.get("source_id") or "")
    preview = str(row.get("preview", "") or "")
    return {
        "handle": format_handle("chunk", chunk_id),
        "type": "chunk",
        "title": "",
        "score": score if score is not None else row.get("score"),
        "rank": None,
        "resource_uri": chunk_uri(chunk_id),
        "preview": preview[:_PREVIEW_CHARS],
        "meta": {
            "doc_handle": format_handle("doc", doc_id) if doc_id else "",
        },
    }


def figure_item(fig: dict) -> dict:
    fig_id = str(fig.get("id", ""))
    doc_id = str(fig.get("source_id", ""))
    return {
        "handle": format_handle("figure", fig_id),
        "type": "figure",
        "title": str(fig.get("caption", "") or "")[:_PREVIEW_CHARS],
        "score": None,
        "rank": None,
        "resource_uri": figure_uri(fig_id),
        "preview": str(fig.get("caption", "") or "")[:_PREVIEW_CHARS],
        "meta": {
            "doc_handle": format_handle("doc", doc_id) if doc_id else "",
            "page": fig.get("page"),
            "path": fig.get("path", ""),
            "near_chunk_handles": [
                format_handle("chunk", cid)
                for cid in fig.get("near_chunk_ids", [])
            ],
        },
    }


def equation_item(eq: dict) -> dict:
    eq_id = str(eq.get("id", ""))
    doc_id = str(eq.get("source_id", ""))
    return {
        "handle": format_handle("equation", eq_id),
        "type": "equation",
        "title": str(eq.get("label", "") or ""),
        "score": None,
        "rank": None,
        "resource_uri": equation_uri(eq_id),
        "preview": str(eq.get("latex", "") or "")[:_PREVIEW_CHARS],
        "meta": {
            "doc_handle": format_handle("doc", doc_id) if doc_id else "",
            "kind": eq.get("kind", ""),
            "is_chemical": bool(eq.get("is_chemical", False)),
            "latex": eq.get("latex", ""),
        },
    }


def author_item(au: dict, *, score: float | None = None,
                in_search_mode: bool = False) -> dict:
    """Build an envelope item for an author dict."""
    key = str(au.get("key", ""))
    rank: dict[str, Any] = {
        "h_index": int(au.get("h_index", 0) or 0),
        "citation_count": int(au.get("citation_count", 0) or 0),
        "n_papers": int(au.get("n_papers", 0) or 0),
    }
    meta: dict[str, Any] = {}
    if in_search_mode:
        meta["n_match"] = int(au.get("n_papers", 0) or 0)
    if au.get("top_coauthors"):
        meta["top_coauthor_handles"] = [
            format_handle("author", str(c.get("key", "")))
            for c in au["top_coauthors"]
        ]
    if score is None and "best_score" in au:
        score = float(au["best_score"])
    return {
        "handle": format_handle("author", key),
        "type": "author",
        "title": str(au.get("name", "") or ""),
        "score": score,
        "rank": rank,
        "resource_uri": author_uri(key),
        "preview": str(au.get("name", "") or ""),
        "meta": meta or None,
    }


# ------------------------------------------------ traversal-row item shaping


def traverse_row_item(row: dict) -> dict:
    """Shape a row from ``queries.traverse_*`` into an envelope item.

    The traversal primitives return heterogeneous rows (sources,
    chunks, figures, equations, authors). Inspect ``type`` to pick a
    builder; default to a minimal source row when type is missing.
    """
    ntype = row.get("type", "source")
    if ntype == "chunk":
        chunk_id = str(row.get("id", ""))
        doc_id = str(row.get("doc_id", ""))
        return {
            "handle": format_handle("chunk", chunk_id),
            "type": "chunk",
            "title": "",
            "score": None,
            "rank": None,
            "resource_uri": chunk_uri(chunk_id),
            "preview": "",
            "meta": {
                "doc_handle": format_handle("doc", doc_id) if doc_id else "",
            },
        }
    if ntype == "figure":
        return figure_item({
            "id": row.get("id", ""),
            "source_id": row.get("doc_id", ""),
            "caption": row.get("caption", ""),
            "page": row.get("page"),
            "path": row.get("path", ""),
            "near_chunk_ids": [],
        })
    if ntype == "equation":
        return equation_item({
            "id": row.get("id", ""),
            "source_id": row.get("doc_id", ""),
            "latex": row.get("latex", ""),
            "label": row.get("label", ""),
            "kind": row.get("kind", ""),
            "is_chemical": row.get("is_chemical", False),
        })
    if ntype == "author":
        return author_item({
            "key": row.get("id", ""),
            "name": row.get("name", ""),
            "h_index": row.get("h_index", 0),
            "citation_count": row.get("citation_count", 0),
            "n_papers": row.get("n_papers", 0),
        })
    # source / unknown -> doc-style row
    doc_id = str(row.get("id", ""))
    return {
        "handle": format_handle("doc", doc_id),
        "type": "doc",
        "title": str(row.get("title", "") or ""),
        "score": None,
        "rank": {
            "citation_count": int(row.get("citation_count", 0) or 0),
            "pagerank": float(row.get("pagerank", 0.0) or 0.0),
        },
        "resource_uri": doc_uri(doc_id),
        "preview": str(row.get("title", "") or "")[:_PREVIEW_CHARS],
        "meta": None,
    }
