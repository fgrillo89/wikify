"""Chunk-id resolution helpers.

The corpus stores every chunk under a canonical long-form id:
    ``<title_hex>__cNNNN_<suffix_hex>``
e.g.
    ``[2015 Matveyev] TiNHfO2_3ce604c2ba54__c0007_d2adf466``

The MCP / CLI layer historically surfaced a short *handle* form:
    ``chunk:<suffix_hex>``
e.g.
    ``chunk:d2adf466``

Figure-chunk handles use a different form:
    ``chunk:<dochex>/fig_NNN__caption``

Evidence records written by explorer subagents often carry handles
instead of canonical ids. This module provides a single resolver that
maps any id/handle form to the canonical chunk_id, building an
in-memory suffix index over the corpus ``chunks`` table once per
call-site.

Public API
----------
``build_suffix_index(corpus_sqlite_path)``
    Returns ``(canonical_ids: frozenset, suffix_to_canonical: dict)``.
    Builds the index once; callers cache the return value if they need
    multiple look-ups.

``resolve_chunk_id(raw, suffix_index, canonical_ids)``
    Map *raw* to the canonical id or ``None`` if unresolvable.
    Accepts already-canonical ids, ``chunk:<hex>`` handles, and
    ``chunk:<dochex>/fig_NNN__caption`` figure handles.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def build_suffix_index(
    sqlite_path: Path,
) -> tuple[frozenset[str], dict[str, str]]:
    """Load all chunk_ids from the corpus SQLite and build a suffix map.

    Returns
    -------
    canonical_ids
        All canonical chunk ids as a frozenset.
    suffix_to_canonical
        Maps the trailing ``_``-delimited suffix of each canonical id to
        the full canonical id.  For ids of the form
        ``<prefix>_<suffix_hex>`` the key is ``<suffix_hex>``.  Entries
        where the suffix would be ambiguous (multiple canonical ids share
        the same suffix) are dropped — the resolver then falls through to
        a SQLite LIKE query for those rare cases.
    """
    if not sqlite_path.exists():
        return frozenset(), {}

    con = sqlite3.connect(str(sqlite_path))
    try:
        rows = con.execute("SELECT chunk_id FROM chunks").fetchall()
    except sqlite3.OperationalError:
        return frozenset(), {}
    finally:
        con.close()

    canonical_ids: set[str] = set()
    suffix_counts: dict[str, int] = {}
    suffix_map: dict[str, str] = {}

    for (cid,) in rows:
        canonical_ids.add(cid)
        # Extract the last ``_``-delimited segment as the suffix.
        parts = cid.rsplit("_", 1)
        if len(parts) == 2:
            suffix = parts[1]
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
            suffix_map[suffix] = cid  # may be overwritten; pruned below

    # Remove ambiguous suffixes.
    for suffix, count in suffix_counts.items():
        if count > 1 and suffix in suffix_map:
            del suffix_map[suffix]

    return frozenset(canonical_ids), suffix_map


def resolve_chunk_id(
    raw: str,
    suffix_index: dict[str, str],
    canonical_ids: frozenset[str],
    *,
    sqlite_path: Path | None = None,
) -> str | None:
    """Map a raw chunk id or handle to the canonical chunk_id.

    Resolution order:
    1. Already canonical: present in ``canonical_ids`` -> return as-is.
    2. ``chunk:<suffix_hex>`` handle: look up in ``suffix_index``.
    3. ``chunk:<dochex>/fig_NNN__caption`` figure handle:
       try it both as-is (exact match) and via suffix index.
    4. Fallback SQLite LIKE query when ``sqlite_path`` is supplied and
       the suffix was ambiguous (not in the in-memory index).

    Returns ``None`` if unresolvable.
    """
    if not raw:
        return None

    # Already canonical.
    if raw in canonical_ids:
        return raw

    # Handle form: chunk:<payload>
    if raw.startswith("chunk:"):
        payload = raw[len("chunk:"):]

        # Figure handle: chunk:<dochex>/fig_NNN__caption
        if "/" in payload:
            # Try exact match first (the full payload might be a canonical id).
            if payload in canonical_ids:
                return payload
            # Try suffix index on the last _-segment of payload.
            parts = payload.rsplit("_", 1)
            if len(parts) == 2 and parts[1] in suffix_index:
                return suffix_index[parts[1]]
            return None

        # Plain hex suffix.
        if payload in suffix_index:
            return suffix_index[payload]

        # Ambiguous suffix: fall back to LIKE query if sqlite_path given.
        if sqlite_path is not None and sqlite_path.exists():
            suffix_esc = (
                payload
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            con = sqlite3.connect(str(sqlite_path))
            try:
                rows = con.execute(
                    "SELECT chunk_id FROM chunks "
                    "WHERE chunk_id LIKE ? ESCAPE '\\'",
                    (f"%_{suffix_esc}",),
                ).fetchall()
            finally:
                con.close()
            if len(rows) == 1:
                return rows[0][0]
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
