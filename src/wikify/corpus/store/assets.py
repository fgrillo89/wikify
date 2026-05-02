"""Assets and chunk_assets CRUD.

Ports the write paths from `corpus/images_index.py` and
`corpus/equations_index.py`. Files (PNGs, sidecars) stay on disk; only
the metadata moves into rows.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def upsert_assets(
    con: sqlite3.Connection,
    doc_id: str,
    assets: list[dict[str, Any]],
) -> None:
    """Replace all `assets` rows belonging to *doc_id*."""
    con.execute("DELETE FROM assets WHERE doc_id = ?", (doc_id,))
    rows = []
    for ord_i, a in enumerate(assets or []):
        rows.append({
            "asset_id": a.get("id") or a.get("asset_id") or f"{doc_id}/asset:{ord_i:04d}",
            "doc_id": doc_id,
            "asset_type": a.get("type") or a.get("asset_type") or "image",
            "ord": a.get("ord", ord_i),
            "page": a.get("page"),
            "path": a.get("path"),
            "caption": a.get("caption"),
            "content": a.get("content"),
            "metadata_json": json.dumps(
                {k: v for k, v in a.items() if k not in {
                    "id", "asset_id", "type", "asset_type", "ord", "page",
                    "path", "caption", "content",
                }},
                default=str,
            ),
        })
    if not rows:
        return
    cols = ",".join(rows[0].keys())
    placeholders = ",".join(":" + k for k in rows[0].keys())
    con.executemany(
        f"INSERT OR REPLACE INTO assets({cols}) VALUES ({placeholders})", rows,
    )


def get_assets(con: sqlite3.Connection, doc_id: str) -> list[dict[str, Any]]:
    return [
        dict(r) for r in con.execute(
            "SELECT * FROM assets WHERE doc_id = ? ORDER BY asset_type, ord",
            (doc_id,),
        )
    ]


def upsert_chunk_assets(
    con: sqlite3.Connection,
    doc_id: str,
    mappings: list[dict[str, Any]],
) -> None:
    """Replace chunk<->asset relations belonging to chunks of *doc_id*."""
    con.execute(
        "DELETE FROM chunk_assets WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)",
        (doc_id,),
    )
    rows = []
    for m in mappings or []:
        rows.append((
            m["chunk_id"], m["asset_id"],
            m.get("relation", "near"),
            float(m["confidence"]) if m.get("confidence") is not None else None,
        ))
    if not rows:
        return
    con.executemany(
        "INSERT OR IGNORE INTO chunk_assets(chunk_id, asset_id, relation, confidence) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def upsert_asset_edges(con: sqlite3.Connection, doc_id: str) -> None:
    """Refresh document/chunk -> asset edges for *doc_id*.

    `document -> asset has_asset`. `chunk -> asset {relation}` (relation
    is the `chunk_assets.relation` value).
    """
    con.execute(
        "DELETE FROM graph_edges WHERE src_type='document' AND src_id=? AND kind='has_asset'",
        (doc_id,),
    )
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('document', ?, 'has_asset', 'asset', ?)",
        [(doc_id, r[0]) for r in con.execute(
            "SELECT asset_id FROM assets WHERE doc_id = ?", (doc_id,),
        )],
    )
    con.execute(
        "DELETE FROM graph_edges WHERE src_type='chunk' AND dst_type='asset' "
        "AND src_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)",
        (doc_id,),
    )
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id) "
        "VALUES ('chunk', ?, ?, 'asset', ?)",
        [
            (r[0], r[2] or "near", r[1])
            for r in con.execute(
                "SELECT ca.chunk_id, ca.asset_id, ca.relation FROM chunk_assets ca "
                "JOIN chunks c ON c.chunk_id = ca.chunk_id WHERE c.doc_id = ?",
                (doc_id,),
            )
        ],
    )
