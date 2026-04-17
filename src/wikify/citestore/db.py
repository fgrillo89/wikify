"""SQLite-backed storage for resolved citation metadata.

Unified cache for all DOI resolution sources (CrossRef batch, doi.org
content negotiation, OpenAlex). DOI is the primary key. Both sync and
async interfaces are provided.
"""

from __future__ import annotations

import json
import sqlite3
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
    source         TEXT NOT NULL DEFAULT '',
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


# ---------------------------------------------------------------------------
# Sync DOI cache (used by cite_parse enrichment)
# ---------------------------------------------------------------------------


class DOICache:
    """Sync SQLite cache for DOI-resolved metadata.

    Shared DB with the async DatabaseManager. Both read/write the same
    ``works`` table. Use this from sync code (cite_parse enrichment);
    use DatabaseManager from async code (OpenAlex resolver).
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> DOICache:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def put(self, doi: str, meta: dict[str, object], source: str = "crossref") -> None:
        """Store resolved metadata for a DOI.

        Uses ``INSERT OR REPLACE`` so a later successful resolution can
        overwrite a prior negative-result row (and so an updated record
        with richer fields replaces a thin one). ``resolved_at`` is reset
        on every write by the schema default.
        """
        assert self._conn
        authors = meta.get("authors") or []
        pages = str(meta.get("pages") or "")
        first_page, last_page = "", ""
        if "--" in pages:
            parts = pages.split("--", 1)
            first_page, last_page = parts[0], parts[1]
        elif "-" in pages:
            parts = pages.split("-", 1)
            first_page, last_page = parts[0], parts[1]
        else:
            first_page = pages

        year = meta.get("year")
        if isinstance(year, str):
            try:
                year = int(year)
            except ValueError:
                year = None

        self._conn.execute(
            """INSERT OR REPLACE INTO works
               (doi, title, year, journal, authors_json, volume,
                first_page, last_page, publisher, source, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                doi.lower(),
                meta.get("title") or "",
                year,
                meta.get("journal") or meta.get("venue") or "",
                json.dumps(authors if isinstance(authors, list) else []),
                meta.get("volume") or "",
                first_page,
                last_page,
                meta.get("publisher") or "",
                source,
            ),
        )
        self._conn.commit()

    # Negative-result rows older than this are treated as cache misses so a
    # DOI that failed to resolve a month ago gets a fresh attempt (the paper
    # may have been registered since). Successful (title-bearing) rows are
    # treated as valid indefinitely — DOIs don't change their metadata.
    NEGATIVE_TTL_DAYS = 14

    def get_many(self, dois: list[str]) -> dict[str, dict[str, object]]:
        """Look up multiple DOIs. Returns ``{doi: metadata}`` for every
        row that exists in the cache, including negative-result rows
        younger than ``NEGATIVE_TTL_DAYS`` (so retries have a bounded
        chance to pick up newly-registered DOIs). Expired negatives
        appear as cache misses.

        Callers that want only usable data should drop rows whose
        ``title`` is empty.
        """
        assert self._conn
        if not dois:
            return {}
        lowered = [d.lower() for d in dois]
        placeholders = ",".join(["?"] * len(lowered))
        rows = self._conn.execute(
            f"SELECT * FROM works WHERE doi IN ({placeholders})",
            lowered,
        ).fetchall()
        out: dict[str, dict[str, object]] = {}
        for row in rows:
            if not row["title"]:
                # Negative row — honour TTL.
                expired = self._conn.execute(
                    "SELECT julianday('now') - julianday(resolved_at) > ? "
                    "FROM works WHERE doi = ?",
                    (self.NEGATIVE_TTL_DAYS, row["doi"]),
                ).fetchone()
                if expired and expired[0]:
                    continue
            out[row["doi"]] = {
                "title": row["title"],
                "authors": json.loads(row["authors_json"]),
                "journal": row["journal"],
                "venue": row["journal"],
                "year": str(row["year"]) if row["year"] else "",
                "volume": row["volume"],
                "pages": (
                    f"{row['first_page']}--{row['last_page']}".strip("-")
                    if row["first_page"] or row["last_page"]
                    else ""
                ),
                "publisher": row["publisher"],
            }
        return out


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
