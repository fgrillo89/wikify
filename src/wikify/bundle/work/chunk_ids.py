"""Chunk-id resolution helpers.

The corpus stores every chunk under a canonical long-form id:
    ``<title_hex>__cNNNN_<suffix_hex>``
e.g.
    ``[2015 Matveyev] TiNHfO2_3ce604c2ba54__c0007_d2adf466``

The MCP / CLI layer also accepts a short *handle* form:
    ``chunk:<suffix_hex>``
e.g.
    ``chunk:d2adf466``

Figure-chunk handles use a different form:
    ``chunk:<dochex>/fig_NNN__caption``

Evidence records written by explorer subagents often carry handles
instead of canonical ids. This module provides a single resolver that
maps any id/handle form to the canonical chunk_id, building an
in-memory ``HandleIndex`` over the corpus ``chunks`` table once per
call-site.

Public API
----------
``build_suffix_index(corpus_sqlite_path)``
    Returns ``(canonical_ids: frozenset, index: HandleIndex)``.
    Builds the index once; callers cache the return value if they need
    multiple look-ups.

``resolve_chunk_id(raw, suffix_index_or_index, canonical_ids)``
    Map *raw* to the canonical id or ``None`` if unresolvable.
    Accepts already-canonical ids, ``chunk:<hex>`` handles, and
    ``chunk:<dochex>/fig_NNN__caption`` figure handles.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ...corpus.handles import (
    AmbiguousHandleError,
    HandleIndex,
    build_index,
    try_resolve,
)


def _build_suffix_index_from_rows(
    chunk_ids: list[str],
) -> tuple[frozenset[str], HandleIndex]:
    """Build canonical-id set and ``HandleIndex`` from a pre-fetched list.

    Returns
    -------
    canonical_ids
        All canonical chunk ids as a frozenset.
    index
        A ``HandleIndex`` built over the same set; used by
        ``resolve_chunk_id`` for O(1) per-lookup resolution.
    """
    idx = build_index(chunk_ids)
    return frozenset(chunk_ids), idx


def build_suffix_index(
    sqlite_path: Path,
) -> tuple[frozenset[str], HandleIndex]:
    """Load all chunk_ids from the corpus SQLite and build a ``HandleIndex``.

    Returns
    -------
    canonical_ids
        All canonical chunk ids as a frozenset.
    index
        A ``HandleIndex`` built over the same set.  The return type
        intentionally mirrors the old ``(frozenset, dict)`` shape so
        existing callers that unpack the pair continue to work; the
        second element is now a ``HandleIndex`` instead of a plain dict.
    """
    if not sqlite_path.exists():
        return frozenset(), HandleIndex()

    con = sqlite3.connect(str(sqlite_path))
    try:
        rows = con.execute("SELECT chunk_id FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return frozenset(), HandleIndex()
    finally:
        con.close()

    return _build_suffix_index_from_rows([cid for (cid,) in rows])


def resolve_chunk_id(
    raw: str,
    suffix_index: dict[str, str] | HandleIndex,
    canonical_ids: frozenset[str],
    *,
    sqlite_path: Path | None = None,
) -> str | None:
    """Map a raw chunk id or handle to the canonical chunk_id.

    Resolution order:
    1. Already canonical: present in ``canonical_ids`` -> return as-is.
    2. ``chunk:<suffix_hex>`` handle: resolve via ``HandleIndex``.
    3. ``chunk:<dochex>/fig_NNN__caption`` figure handle:
       try exact match then resolve the compound id via ``HandleIndex``.

    The ``sqlite_path`` parameter is accepted for backward compatibility
    but is no longer used; resolution is entirely in-memory via the
    ``HandleIndex``.

    Returns ``None`` if unresolvable.
    """
    if not raw:
        return None

    # Already canonical.
    if raw in canonical_ids:
        return raw

    # Coerce a legacy plain-dict suffix_index to a HandleIndex so this
    # function works whether callers pass the old dict or the new index.
    if not isinstance(suffix_index, HandleIndex):
        index: HandleIndex = build_index(canonical_ids)
    else:
        index = suffix_index

    # Handle form: chunk:<payload>
    if raw.startswith("chunk:"):
        payload = raw[len("chunk:"):]

        # Figure handle: chunk:<dochex>/fig_NNN__caption
        if "/" in payload:
            # Try exact match first (the full payload might be a canonical id).
            if payload in canonical_ids:
                return payload
            # Resolve the compound payload as a short handle.
            result = try_resolve(payload, index)
            return result

        # Plain hex suffix: resolve as short handle.
        try:
            return try_resolve(payload, index)
        except AmbiguousHandleError:
            return None

    # Bare suffix without the "chunk:" prefix? Treat as unknown.
    return None


def corpus_path_from_bundle(bundle_root: Path) -> Path | None:
    """Read the corpus path recorded in ``run/state.json``.

    Returns ``None`` if the state file is absent or unreadable.
    """
    import json

    state_path = bundle_root / "run" / "state.json"
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        corpus_str = data.get("corpus_path")
        if not corpus_str:
            return None
        p = Path(corpus_str)
        # Absolute paths are used as-is. Relative paths are stored
        # relative to the working directory the run was launched from
        # (typically the repo root), so try that first, then fall back
        # to resolving against the bundle root.
        if p.is_absolute():
            return p if p.is_dir() else None
        if p.is_dir():
            return p
        alt = bundle_root / p
        return alt if alt.is_dir() else None
    except Exception:
        return None
