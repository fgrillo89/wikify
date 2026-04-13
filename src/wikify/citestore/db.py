"""SQLite-backed storage for resolved citation metadata."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from .models import Work

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS works (
    doi            TEXT PRIMARY KEY,
    openalex_id    TEXT UNIQUE,
    title          TEXT NOT NULL DEFAULT '',
    year           INTEGER,
    journal        TEXT NOT NULL DEFAULT '',
    authors_json   TEXT NOT NULL DEFAULT '[]',
    volume         TEXT NOT NULL DEFAULT '',
    issue          TEXT NOT NULL DEFAULT '',
    first_page     TEXT NOT NULL DEFAULT '',
    last_page      TEXT NOT NULL DEFAULT '',
    publisher      TEXT NOT NULL DEFAULT '',
    cited_by_count INTEGER,
    work_type      TEXT NOT NULL DEFAULT '',
    bibtex         TEXT NOT NULL DEFAULT '',
    raw_json       TEXT NOT NULL DEFAULT '{}',
    resolved_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_works_openalex ON works(openalex_id);

CREATE TABLE IF NOT EXISTS string_cache (
    sha256           TEXT PRIMARY KEY,
    raw_text         TEXT NOT NULL,
    resolved_doi     TEXT,
    resolution_level TEXT NOT NULL DEFAULT '',
    resolved_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS citation_edges (
    parent_doi TEXT NOT NULL,
    child_doi  TEXT NOT NULL,
    PRIMARY KEY (parent_doi, child_doi)
);
"""


class DatabaseManager:
    """Async SQLite manager for citation metadata."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> DatabaseManager:
        await self.init()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "DatabaseManager not initialized"
        return self._conn

    # ---- works ----

    def _row_to_work(self, row: aiosqlite.Row) -> Work:
        return Work(
            doi=row["doi"],
            openalex_id=row["openalex_id"] or "",
            title=row["title"],
            year=row["year"],
            journal=row["journal"],
            authors=json.loads(row["authors_json"]),
            volume=row["volume"],
            issue=row["issue"],
            first_page=row["first_page"],
            last_page=row["last_page"],
            publisher=row["publisher"],
            cited_by_count=row["cited_by_count"],
            work_type=row["work_type"],
            bibtex=row["bibtex"],
            raw=json.loads(row["raw_json"]),
        )

    async def get_work(self, doi: str) -> Work | None:
        cursor = await self.conn.execute(
            "SELECT * FROM works WHERE doi = ?", (doi,)
        )
        row = await cursor.fetchone()
        return self._row_to_work(row) if row else None

    async def get_work_by_openalex(self, oa_id: str) -> Work | None:
        cursor = await self.conn.execute(
            "SELECT * FROM works WHERE openalex_id = ?", (oa_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_work(row) if row else None

    async def upsert_work(self, work: Work) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO works
               (doi, openalex_id, title, year, journal, authors_json,
                volume, issue, first_page, last_page, publisher,
                cited_by_count, work_type, bibtex, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                work.doi,
                work.openalex_id,
                work.title,
                work.year,
                work.journal,
                json.dumps(work.authors),
                work.volume,
                work.issue,
                work.first_page,
                work.last_page,
                work.publisher,
                work.cited_by_count,
                work.work_type,
                work.bibtex,
                json.dumps(work.raw),
            ),
        )
        await self.conn.commit()

    async def upsert_works(self, works: list[Work]) -> None:
        if not works:
            return
        await self.conn.execute("BEGIN")
        try:
            await self.conn.executemany(
                """INSERT OR REPLACE INTO works
                   (doi, openalex_id, title, year, journal, authors_json,
                    volume, issue, first_page, last_page, publisher,
                    cited_by_count, work_type, bibtex, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        w.doi,
                        w.openalex_id,
                        w.title,
                        w.year,
                        w.journal,
                        json.dumps(w.authors),
                        w.volume,
                        w.issue,
                        w.first_page,
                        w.last_page,
                        w.publisher,
                        w.cited_by_count,
                        w.work_type,
                        w.bibtex,
                        json.dumps(w.raw),
                    )
                    for w in works
                ],
            )
            await self.conn.execute("COMMIT")
        except Exception:
            await self.conn.execute("ROLLBACK")
            raise

    # ---- string_cache ----

    async def get_cached_resolution(
        self, sha256: str
    ) -> tuple[str | None, str] | None:
        """Return (resolved_doi, resolution_level) or None if not cached."""
        cursor = await self.conn.execute(
            "SELECT resolved_doi, resolution_level FROM string_cache WHERE sha256 = ?",
            (sha256,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["resolved_doi"], row["resolution_level"]

    async def cache_resolution(
        self,
        sha256: str,
        raw_text: str,
        doi: str | None,
        level: str,
    ) -> None:
        await self.conn.execute(
            """INSERT OR REPLACE INTO string_cache
               (sha256, raw_text, resolved_doi, resolution_level)
               VALUES (?, ?, ?, ?)""",
            (sha256, raw_text, doi, level),
        )
        await self.conn.commit()

    # ---- citation_edges ----

    async def add_edges(self, parent_doi: str, child_dois: list[str]) -> None:
        if not child_dois:
            return
        await self.conn.execute("BEGIN")
        try:
            await self.conn.executemany(
                "INSERT OR IGNORE INTO citation_edges (parent_doi, child_doi) VALUES (?, ?)",
                [(parent_doi, c) for c in child_dois],
            )
            await self.conn.execute("COMMIT")
        except Exception:
            await self.conn.execute("ROLLBACK")
            raise

    # ---- bulk reads ----

    async def get_all_works(self) -> list[Work]:
        cursor = await self.conn.execute("SELECT * FROM works")
        return [self._row_to_work(r) for r in await cursor.fetchall()]

    async def get_all_edges(self) -> list[tuple[str, str]]:
        cursor = await self.conn.execute(
            "SELECT parent_doi, child_doi FROM citation_edges"
        )
        return [(r["parent_doi"], r["child_doi"]) for r in await cursor.fetchall()]

    async def known_openalex_ids(self) -> set[str]:
        """Return the set of OpenAlex IDs already stored."""
        cursor = await self.conn.execute(
            "SELECT openalex_id FROM works WHERE openalex_id IS NOT NULL AND openalex_id != ''"
        )
        return {r["openalex_id"] for r in await cursor.fetchall()}
