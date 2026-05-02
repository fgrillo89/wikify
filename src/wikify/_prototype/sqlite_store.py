"""In-memory SQLite query-store prototype against the tiny fixture.

Builds the schema in :memory:, ingests tests/fixtures/tiny/*.md as documents
and paragraph chunks, embeds with a deterministic hash embedder, and runs one
BM25 query, one vector matmul, and one recursive-CTE traversal. EXPLAIN QUERY
PLAN is captured for each so the index choice is visible.

Smoke test:  uv run python -m wikify._prototype.sqlite_store
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "tiny"
DIM = 64

DDL = """
CREATE TABLE documents (doc_id TEXT PRIMARY KEY, title TEXT, abstract TEXT);
CREATE TABLE chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ord INTEGER, text TEXT NOT NULL
);
CREATE INDEX chunks_doc_ord ON chunks(doc_id, ord);
CREATE TABLE embeddings (node_id TEXT PRIMARY KEY, vector BLOB NOT NULL);
CREATE TABLE graph_edges (
  src_type TEXT NOT NULL, src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,
  PRIMARY KEY (src_type, src_id, kind, dst_type, dst_id)
);
CREATE INDEX graph_out ON graph_edges(src_type, src_id, kind);
CREATE INDEX graph_in  ON graph_edges(dst_type, dst_id, kind);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, content='chunks', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);
"""


def _embed(text: str) -> np.ndarray:
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def build() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(DDL)
    for md in sorted(FIXTURE.glob("*.md")):
        body = md.read_text(encoding="utf-8")
        title_m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        doc_id = md.stem
        con.execute(
            "INSERT INTO documents VALUES (?,?,?)",
            (doc_id, title_m.group(1) if title_m else doc_id, body[:240]),
        )
        for i, txt in enumerate(p.strip() for p in re.split(r"\n\n+", body) if p.strip()):
            cid = f"{doc_id}:{i}"
            con.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, doc_id, i, txt))
            con.execute("INSERT INTO embeddings VALUES (?,?)", (cid, _embed(txt).tobytes()))
            con.execute(
                "INSERT INTO graph_edges VALUES ('document',?,?,'chunk',?)",
                (doc_id, "has_chunk", cid),
            )
    refs = [
        ("ald", "catalysis"),
        ("catalysis", "photocatalysis"),
        ("photocatalysis", "water_splitting"),
    ]
    con.executemany(
        "INSERT OR IGNORE INTO graph_edges VALUES ('document',?, 'references','document',?)",
        refs,
    )
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    return con


def demo(con: sqlite3.Connection) -> None:
    print("== BM25: 'titanium dioxide' ==")
    sql_bm = (
        "SELECT chunks.chunk_id, bm25(chunks_fts) s FROM chunks_fts "
        "JOIN chunks ON chunks.rowid = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ? ORDER BY s LIMIT 3"
    )
    for r in con.execute(sql_bm, ("titanium dioxide",)):
        print(" ", r)
    print("plan:", con.execute("EXPLAIN QUERY PLAN " + sql_bm, ("titanium dioxide",)).fetchall())

    print("== Vector matmul: 'atomic layer deposition' ==")
    ids, blobs = zip(*con.execute("SELECT node_id, vector FROM embeddings"))
    mat = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(ids), DIM)
    scores = mat @ _embed("atomic layer deposition")
    for i in np.argsort(-scores)[:3]:
        print(" ", ids[i], float(scores[i]))

    print("== Recursive CTE traverse from document=ald via references ==")
    cte = (
        "WITH RECURSIVE walk(depth,node_type,node_id) AS ("
        " SELECT 0,'document','ald' UNION "
        " SELECT walk.depth+1, e.dst_type, e.dst_id FROM walk "
        " JOIN graph_edges e ON e.src_type=walk.node_type AND e.src_id=walk.node_id "
        " WHERE walk.depth < 3 AND e.kind = 'references'"
        ") SELECT * FROM walk WHERE depth>0 LIMIT 10"
    )
    print(con.execute(cte).fetchall())
    print("plan:", con.execute("EXPLAIN QUERY PLAN " + cte).fetchall())


if __name__ == "__main__":
    demo(build())
