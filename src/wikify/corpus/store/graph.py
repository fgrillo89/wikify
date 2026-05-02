"""GraphStore: graph_edges access via index lookup or recursive CTE.

`neighbors` is one indexed read against `graph_out` / `graph_in`.
`traverse` and `subgraph` use the UNION-with-depth-cap CTE; `path` uses
the path-tracking CTE only when callers actually need the path.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass
class Edge:
    src_type: str
    src_id: str
    kind: str
    dst_type: str
    dst_id: str
    weight: float = 1.0
    ord: int | None = None
    meta: dict[str, Any] | None = None


def _kind_clause(kinds: Iterable[str] | None, params: list[Any]) -> str:
    if not kinds:
        return ""
    klist = list(kinds)
    if len(klist) == 1:
        params.append(klist[0])
        return " AND e.kind = ?"
    placeholders = ",".join("?" * len(klist))
    params.extend(klist)
    return f" AND e.kind IN ({placeholders})"


class GraphStore:
    """Thin wrapper around graph_edges that gives the rest of wikify a
    backend-agnostic graph surface."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def upsert_edges(self, edges: Iterable[Edge]) -> None:
        rows = []
        for e in edges:
            import json as _json
            rows.append((
                e.src_type, e.src_id, e.kind, e.dst_type, e.dst_id,
                float(e.weight), e.ord,
                _json.dumps(e.meta) if e.meta else None,
            ))
        if not rows:
            return
        self.con.executemany(
            "INSERT OR REPLACE INTO graph_edges(src_type, src_id, kind, dst_type, dst_id, "
            "weight, ord, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def delete_outgoing(
        self, src_type: str, src_id: str, kinds: Iterable[str] | None = None,
    ) -> None:
        params: list[Any] = [src_type, src_id]
        sql = "DELETE FROM graph_edges WHERE src_type=? AND src_id=?"
        if kinds:
            klist = list(kinds)
            sql += " AND kind IN (" + ",".join("?" * len(klist)) + ")"
            params.extend(klist)
        self.con.execute(sql, params)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def neighbors(
        self,
        node_type: str,
        node_id: str,
        *,
        direction: str = "out",
        kinds: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """One indexed lookup against graph_out (direction='out') or graph_in."""
        params: list[Any] = [node_type, node_id]
        if direction == "out":
            sql = "SELECT * FROM graph_edges e WHERE e.src_type=? AND e.src_id=?"
        elif direction == "in":
            sql = "SELECT * FROM graph_edges e WHERE e.dst_type=? AND e.dst_id=?"
        else:
            raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")
        sql += _kind_clause(kinds, params)
        sql += " LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.con.execute(sql, params)]

    def traverse(
        self,
        seeds: Iterable[tuple[str, str]],
        *,
        direction: str = "out",
        kinds: Iterable[str] | None = None,
        max_depth: int = 2,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """UNION-based recursive walk with depth cap; no path tracking.

        Visits each (node_type, node_id) at most once per CTE evaluation;
        cycle-safe by virtue of UNION (not UNION ALL).
        """
        seeds = list(seeds)
        if not seeds:
            return []
        if direction not in {"out", "in"}:
            raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")

        join_cond = (
            "e.src_type=walk.node_type AND e.src_id=walk.node_id"
            if direction == "out"
            else "e.dst_type=walk.node_type AND e.dst_id=walk.node_id"
        )
        next_node = "e.dst_type, e.dst_id" if direction == "out" else "e.src_type, e.src_id"

        seed_select = " UNION ".join(["SELECT 0, ?, ?"] * len(seeds))
        params: list[Any] = []
        for st, sid in seeds:
            params.extend([st, sid])
        params.append(max_depth)
        cte_kind = _kind_clause(kinds, params)
        params.append(limit)

        sql = (
            f"WITH RECURSIVE walk(depth, node_type, node_id) AS ("
            f" {seed_select} UNION "
            f" SELECT walk.depth + 1, {next_node} FROM walk "
            f" JOIN graph_edges e ON {join_cond} "
            f" WHERE walk.depth < ?{cte_kind}"
            f") SELECT depth, node_type, node_id FROM walk WHERE depth > 0 LIMIT ?"
        )
        return [dict(r) for r in self.con.execute(sql, params)]

    def subgraph(
        self,
        seeds: Iterable[tuple[str, str]],
        *,
        kinds: Iterable[str] | None = None,
        depth: int = 1,
        limit: int = 500,
    ) -> dict[str, list[dict[str, Any]]]:
        """Materialize the edges within `depth` hops from the seeds."""
        nodes = self.traverse(seeds, direction="out", kinds=kinds, max_depth=depth, limit=limit)
        nodes = [{"depth": 0, "node_type": st, "node_id": sid} for st, sid in seeds] + nodes
        # Pull every edge whose endpoints both lie in the visited set.
        seen = {(n["node_type"], n["node_id"]) for n in nodes}
        edges: list[dict[str, Any]] = []
        for r in self.con.execute("SELECT * FROM graph_edges"):
            if (r["src_type"], r["src_id"]) in seen and (r["dst_type"], r["dst_id"]) in seen:
                edges.append(dict(r))
        return {"nodes": nodes, "edges": edges}

    def path(
        self,
        start: tuple[str, str],
        target: tuple[str, str],
        *,
        kinds: Iterable[str] | None = None,
        max_depth: int = 4,
    ) -> list[tuple[str, str]] | None:
        """Path-tracking variant: returns the first path if one exists.

        Slower than `traverse`. Uses `instr()` cycle guard on the trail
        string so we don't emit revisiting paths.
        """
        params: list[Any] = [start[0], start[1]]
        params.append(max_depth)
        kind_clause = _kind_clause(kinds, params)
        params.extend([target[0], target[1]])

        sql = (
            "WITH RECURSIVE walk(depth, node_type, node_id, trail) AS ("
            "  SELECT 0, ?, ?, '|' || ? || '::' || ? || '|' "
            "    FROM (SELECT 1) "
            "  UNION ALL "
            "  SELECT walk.depth + 1, e.dst_type, e.dst_id, "
            "         walk.trail || e.dst_type || '::' || e.dst_id || '|' "
            "  FROM walk JOIN graph_edges e "
            "    ON e.src_type=walk.node_type AND e.src_id=walk.node_id "
            "  WHERE walk.depth < ?"
            f"   {kind_clause}"
            "    AND instr(walk.trail, '|' || e.dst_type || '::' || e.dst_id || '|') = 0"
            ") SELECT trail FROM walk WHERE node_type=? AND node_id=? "
            "  ORDER BY depth ASC LIMIT 1"
        )
        # The seed select needs the start values twice (for placeholder pairs).
        # Rewrite params accordingly.
        params = [start[0], start[1], start[0], start[1], max_depth]
        kind_params: list[Any] = []
        kind_clause2 = _kind_clause(kinds, kind_params)
        params.extend(kind_params)
        params.extend([target[0], target[1]])
        sql = (
            "WITH RECURSIVE walk(depth, node_type, node_id, trail) AS ("
            "  VALUES (0, ?, ?, '|' || ? || '::' || ? || '|') "
            "  UNION ALL "
            "  SELECT walk.depth + 1, e.dst_type, e.dst_id, "
            "         walk.trail || e.dst_type || '::' || e.dst_id || '|' "
            "  FROM walk JOIN graph_edges e "
            "    ON e.src_type=walk.node_type AND e.src_id=walk.node_id "
            "  WHERE walk.depth < ?"
            f"   {kind_clause2}"
            "    AND instr(walk.trail, '|' || e.dst_type || '::' || e.dst_id || '|') = 0"
            ") SELECT trail FROM walk WHERE node_type=? AND node_id=? "
            "  ORDER BY depth ASC LIMIT 1"
        )
        row = self.con.execute(sql, params).fetchone()
        if not row:
            return None
        trail = row[0].strip("|")
        steps: list[tuple[str, str]] = []
        for token in trail.split("|"):
            if not token:
                continue
            t, _, i = token.partition("::")
            steps.append((t, i))
        return steps
