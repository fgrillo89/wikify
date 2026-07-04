"""Corpus-side helpers for the factual-data subsystem.

Two jobs:

1. Provide the source text a data point must verify against — the chunk text
   plus any table/figure asset captions and table markdown bound to that chunk.
2. Enumerate candidate sources for the dedicated harvest pass — table assets
   and number-dense chunks — so an extractor agent knows where the numbers are.

Read-only; opens the corpus SQLite directly.
"""

from __future__ import annotations

import re
import sqlite3

from ..api import Corpus
from ..corpus.chunks import read_chunks_by_id

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _resolve_handle(corpus: Corpus, chunk_id: str) -> str:
    """Best-effort resolve a short ``chunk:<hex>`` handle to its canonical id.

    Returns the input unchanged when it cannot be resolved (so callers can
    fall through to their existing behaviour). Import is local to avoid a
    module-load cycle through ``corpus.queries``.
    """
    from ..corpus.queries import resolve_chunk_id

    try:
        return resolve_chunk_id(corpus, chunk_id)
    except LookupError:
        # HandleNotFoundError / AmbiguousHandleError both subclass LookupError;
        # an unresolvable handle falls through to the caller's empty-source path.
        return chunk_id


def _connect(corpus: Corpus) -> sqlite3.Connection | None:
    db = corpus.sqlite_path
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def _near_asset_text(con: sqlite3.Connection, chunk_id: str) -> str:
    """Captions + markdown content of assets bound to *chunk_id*."""
    rows = con.execute(
        "SELECT a.caption, a.content FROM chunk_assets ca "
        "JOIN assets a ON a.asset_id = ca.asset_id "
        "WHERE ca.chunk_id = ?",
        (chunk_id,),
    ).fetchall()
    parts: list[str] = []
    for r in rows:
        if r["caption"]:
            parts.append(str(r["caption"]))
        if r["content"]:
            parts.append(str(r["content"]))
    return "\n".join(parts)


def source_text_for(
    corpus: Corpus,
    *,
    doc_id: str,
    chunk_id: str = "",
    locator: str = "",
) -> tuple[str, str, str]:
    """Return ``(chunk_text, asset_text, canonical_doc_id)`` for a data point.

    ``chunk_text`` is the cited chunk's body. ``asset_text`` concatenates the
    captions + table markdown of assets bound to that chunk (where caption
    numbers and table cells live). ``canonical_doc_id`` is the resolved
    chunk's own ``doc_id`` (so claims store the same canonical form as
    evidence and downstream joins line up); it falls back to the supplied
    ``doc_id`` when the chunk cannot be resolved. Any field may be empty.
    """
    chunk_text = ""
    canonical_doc_id = doc_id
    if chunk_id:
        chunks = read_chunks_by_id(corpus, [chunk_id])
        if not chunks:
            # The id may be a short ``chunk:<hex>`` handle (the form the MCP
            # corpus tools return). ``read_chunks_by_id`` is an exact match, so
            # resolve the handle to its canonical id and retry — otherwise the
            # source text comes back empty and the point is wrongly rejected.
            resolved = _resolve_handle(corpus, chunk_id)
            if resolved and resolved != chunk_id:
                chunks = read_chunks_by_id(corpus, [resolved])
        if chunks:
            chunk_text = chunks[0].text
            if chunks[0].doc_id:
                canonical_doc_id = chunks[0].doc_id
    con = _connect(corpus)
    if con is None:
        return chunk_text, "", canonical_doc_id
    try:
        asset_text = _near_asset_text(con, chunk_id) if chunk_id else ""
        # Fall back to all of the doc's table assets when the chunk has no
        # bound assets but the point claims a table/caption source.
        if not asset_text and locator:
            rows = con.execute(
                "SELECT caption, content FROM assets "
                "WHERE doc_id = ? AND asset_type IN ('table','figure','scheme')",
                (canonical_doc_id,),
            ).fetchall()
            asset_text = "\n".join(
                str(r["caption"] or "") + "\n" + str(r["content"] or "")
                for r in rows
            )
        return chunk_text, asset_text, canonical_doc_id
    finally:
        con.close()


def list_table_assets(corpus: Corpus, doc_ids: list[str] | None = None) -> list[dict]:
    """Table (and scheme) assets, optionally restricted to *doc_ids*."""
    con = _connect(corpus)
    if con is None:
        return []
    try:
        sql = (
            "SELECT asset_id, doc_id, caption, content, page FROM assets "
            "WHERE asset_type IN ('table','scheme')"
        )
        params: list[object] = []
        if doc_ids:
            ph = ",".join("?" * len(doc_ids))
            sql += f" AND doc_id IN ({ph})"
            params.extend(doc_ids)
        sql += " ORDER BY doc_id, ord"
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def _needle_set(*groups: list[str]) -> list[str]:
    """Deduped, lowercased match needles from alias/unit groups.

    Each alias is expanded to its separator variants: internal whitespace is
    collapsed and spaces and hyphens are treated as interchangeable, so a
    caller that supplies ``"growth per cycle"`` also matches
    ``"growth-per-cycle"`` (and vice versa) without enumerating every
    spelling. The needle set is deduped, so a chunk that matches several
    aliases is still counted once by the sweep. (The acronym and the expanded
    form -- ``"gpc"`` vs ``"growth per cycle"`` -- are genuinely different
    strings; supply BOTH as aliases.)
    """
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for raw in group or []:
            base = re.sub(r"\s+", " ", (raw or "").strip().lower())
            if not base:
                continue
            spaced = re.sub(r"\s+", " ", base.replace("-", " ")).strip()
            hyphened = spaced.replace(" ", "-")
            for n in (base, spaced, hyphened):
                if n and n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def sweep_property_candidates(
    corpus: Corpus,
    *,
    phrasings: list[str],
    units: list[str],
    max_chunks: int = 500,
    include_text: bool = False,
) -> dict:
    """Whole-corpus enumeration of chunks that mention a single property.

    Scans EVERY document (never a doc-list slice): a chunk is a candidate when
    its own text, or the caption/markdown of a ``table``/``scheme``/``figure``
    asset bound to it, contains any alias *phrasing* or *unit*. Matching is a
    cheap ``instr`` substring test that returns handles only unless
    *include_text* is set, so the extractor pays the read cost only for the
    candidates it verifies.

    Returns ``{candidates, docs_mentioning, candidate_chunks, matched_chunks,
    truncated}``. ``candidates`` is a deterministic, ``max_chunks``-capped list
    of ``{doc_id, chunk_id, matched_phrasing, source_kind[, text]}`` rows.
    ``docs_mentioning`` is the FULL distinct-doc set (independent of the cap) so
    the recall denominator stays exact even when the candidate list truncates.
    """
    needles = _needle_set(phrasings, units)
    empty = {
        "candidates": [], "docs_mentioning": [], "candidate_chunks": 0,
        "matched_chunks": 0, "truncated": False,
    }
    con = _connect(corpus)
    if con is None or not needles:
        return empty
    try:
        # chunk_id -> (doc_id, matched_phrasing, source_kind); first hit wins.
        hits: dict[str, tuple[str, str, str]] = {}
        docs: set[str] = set()
        for needle in needles:
            for r in con.execute(
                "SELECT chunk_id, doc_id FROM chunks "
                "WHERE COALESCE(is_boilerplate, 0) = 0 "
                "AND instr(lower(text), ?) > 0",
                (needle,),
            ):
                docs.add(r["doc_id"])
                hits.setdefault(r["chunk_id"], (r["doc_id"], needle, "text"))
            for r in con.execute(
                "SELECT a.doc_id AS doc_id, ca.chunk_id AS chunk_id FROM assets a "
                "LEFT JOIN chunk_assets ca ON ca.asset_id = a.asset_id "
                "WHERE a.asset_type IN ('table','scheme','figure') "
                "AND instr(lower(COALESCE(a.caption,'') || ' ' || "
                "COALESCE(a.content,'')), ?) > 0",
                (needle,),
            ):
                # Only count an asset-bearing doc as "mentioning" when the
                # asset is bound to a chunk the extractor can actually reach
                # and cite; an unbound asset would inflate the recall
                # denominator with a doc that yields no candidate.
                cid = r["chunk_id"]
                if cid:
                    docs.add(r["doc_id"])
                    hits.setdefault(cid, (r["doc_id"], needle, "asset"))
        ordered = sorted(hits.items(), key=lambda kv: (kv[1][0], kv[0]))
        matched = len(ordered)
        truncated = matched > max_chunks
        ordered = ordered[:max_chunks]
        text_map: dict[str, str] = {}
        if include_text and ordered:
            ids = [cid for cid, _ in ordered]
            ph = ",".join("?" * len(ids))
            for r in con.execute(
                f"SELECT chunk_id, text FROM chunks WHERE chunk_id IN ({ph})", ids
            ):
                text_map[r["chunk_id"]] = r["text"]
        candidates: list[dict] = []
        for cid, (doc_id, needle, kind) in ordered:
            row = {
                "doc_id": doc_id, "chunk_id": cid,
                "matched_phrasing": needle, "source_kind": kind,
            }
            if include_text:
                row["text"] = text_map.get(cid, "")
            candidates.append(row)
        return {
            "candidates": candidates,
            "docs_mentioning": sorted(docs),
            "candidate_chunks": len(candidates),
            "matched_chunks": matched,
            "truncated": truncated,
        }
    finally:
        con.close()


def count_numbers(text: str) -> int:
    return len(_NUM_RE.findall(text or ""))


def number_dense_chunks(
    corpus: Corpus,
    *,
    doc_ids: list[str] | None = None,
    min_numbers: int = 4,
    limit: int = 50,
) -> list[dict]:
    """Chunks rich in numeric tokens — likely to carry extractable facts.

    Excludes boilerplate and reference/acknowledgment sections. Ordered by
    number density (descending).
    """
    con = _connect(corpus)
    if con is None:
        return []
    try:
        sql = (
            "SELECT chunk_id, doc_id, text, section_type FROM chunks "
            "WHERE COALESCE(is_boilerplate, 0) = 0 "
            "AND COALESCE(section_type,'body') NOT IN "
            "('references','acknowledgments','appendix')"
        )
        params: list[object] = []
        if doc_ids:
            ph = ",".join("?" * len(doc_ids))
            sql += f" AND doc_id IN ({ph})"
            params.extend(doc_ids)
        scored: list[dict] = []
        for r in con.execute(sql, params):
            n = count_numbers(r["text"])
            if n >= min_numbers:
                scored.append({
                    "chunk_id": r["chunk_id"],
                    "doc_id": r["doc_id"],
                    "section_type": r["section_type"],
                    "n_numbers": n,
                    "text": r["text"],
                })
        scored.sort(key=lambda d: d["n_numbers"], reverse=True)
        return scored[:limit]
    finally:
        con.close()
