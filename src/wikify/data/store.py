"""SQLite claim store for the factual-data subsystem (`<bundle>/claims.db`).

Schema-on-read: ``data_points`` carries a thin validated core (subject /
property / value / unit / provenance) with an open ``conditions_json`` blob
and a typed ``property_registry`` catalog over the property space. Data
artifacts are stored as durable specs plus their backing claim ids; the
rendered table is always a projection re-derived from these rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..corpus.store.connection import connect, transaction
from .models import ArtifactSpec, DataPoint, normalize_key

DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_points (
  claim_id TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  subject_norm TEXT NOT NULL,
  property TEXT NOT NULL,
  property_norm TEXT NOT NULL,
  value_num REAL,
  value_text TEXT NOT NULL,
  unit TEXT,
  value_original TEXT,
  unit_original TEXT,
  uncertainty TEXT,
  value_type TEXT DEFAULT 'scalar',
  conditions_json TEXT,
  method TEXT,
  doc_id TEXT NOT NULL,
  chunk_id TEXT,
  locator TEXT,
  grounding_quote TEXT NOT NULL,
  quote_verified INTEGER DEFAULT 0,
  source_kind TEXT DEFAULT 'text',
  extraction_tier TEXT DEFAULT 'T1',
  verification_status TEXT DEFAULT 'unverified',
  confidence REAL,
  extractor TEXT,
  round INTEGER,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS data_points_subject ON data_points(subject_norm);
CREATE INDEX IF NOT EXISTS data_points_property ON data_points(property_norm);
CREATE INDEX IF NOT EXISTS data_points_doc ON data_points(doc_id);
CREATE INDEX IF NOT EXISTS data_points_chunk ON data_points(chunk_id);

CREATE TABLE IF NOT EXISTS property_registry (
  property_norm TEXT PRIMARY KEY,
  canonical_unit TEXT,
  quantity_kind TEXT,
  description TEXT,
  n_points INTEGER DEFAULT 0,
  aliases_json TEXT
);

CREATE TABLE IF NOT EXISTS data_artifacts (
  artifact_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  spec_json TEXT NOT NULL,
  status TEXT DEFAULT 'draft',
  n_rows INTEGER DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS data_artifact_claims (
  artifact_id TEXT NOT NULL REFERENCES data_artifacts(artifact_id) ON DELETE CASCADE,
  claim_id TEXT NOT NULL,
  PRIMARY KEY (artifact_id, claim_id)
);
CREATE INDEX IF NOT EXISTS data_artifact_claims_claim ON data_artifact_claims(claim_id);

CREATE TABLE IF NOT EXISTS property_sweeps (
  property_norm TEXT PRIMARY KEY,
  property TEXT,
  docs_mentioning INTEGER DEFAULT 0,
  docs_extracted INTEGER DEFAULT 0,
  docs_in_table INTEGER DEFAULT 0,
  candidate_chunks INTEGER DEFAULT 0,
  truncated INTEGER DEFAULT 0,
  last_sweep TEXT
);
"""

_POINT_COLS = [
    "claim_id", "subject", "subject_norm", "property", "property_norm",
    "value_num", "value_text", "unit", "value_original", "unit_original",
    "uncertainty", "value_type", "conditions_json", "method", "doc_id",
    "chunk_id", "locator", "grounding_quote", "quote_verified", "source_kind",
    "extraction_tier", "verification_status", "confidence", "extractor",
    "round", "created_at",
]


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _natural_key(row: dict) -> tuple:
    """Identity of the FACT a row asserts, independent of its ``claim_id``.

    The same fields ``DataPoint._content_hash`` hashes, read off a stored row
    (or a fresh point's ``to_row()``) rather than off the id. Used so duplicate
    detection still works on rows whose stored id no longer reproduces from
    their content.
    """
    value_num = row.get("value_num")
    value_key = (
        repr(round(float(value_num), 6))
        if value_num is not None
        else str(row.get("value_text") or "").strip().lower()
    )
    return (
        row.get("doc_id") or "",
        row.get("chunk_id") or "",
        normalize_key(str(row.get("subject") or "")),
        normalize_key(str(row.get("property") or "")),
        value_key,
        str(row.get("unit") or "").strip().lower(),
        str(row.get("uncertainty") or "").strip().lower(),
        str(row.get("value_type") or "").strip().lower(),
        row.get("conditions_json") or "{}",
    )


class DataStore:
    """Read/write facade over ``<bundle>/claims.db``."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.con = connect(self.path)
        self.con.executescript(DATA_SCHEMA)

    @classmethod
    def open(cls, bundle_root: str | Path) -> DataStore:
        return cls(Path(bundle_root) / "claims.db")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> DataStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- claims ----------------------------------------------------------

    def add_points(self, points: list[DataPoint]) -> dict:
        """Insert finalized data points; idempotent per stored fact.

        Caller is responsible for running the verify gate first. Points already
        present are reported as duplicates and left untouched. Returns counts +
        the inserted claim ids.

        Duplicate detection matches on ``claim_id`` AND on the natural key the
        id hashes. The second test covers the case where a stored row's
        ``claim_id`` no longer reproduces from its own content:
        ``relabel_point`` rewrites a label while deliberately keeping the id
        (artifact links reference it), so a re-extraction under the new label
        computes a different hash. Matching on the id alone would insert a
        second row for the same fact, inflating ``n_points`` and making a table
        conflict with itself from one chunk.

        It does NOT cover a change to how ``value_num`` is parsed: the stored
        row's natural key is derived from the stored value, so an old row and a
        newly-parsed point differ on both keys and dedup cannot fire either way.
        """
        inserted: list[str] = []
        duplicate = 0
        existing = {
            r[0] for r in self.con.execute("SELECT claim_id FROM data_points")
        }
        existing_natural = {
            _natural_key(dict(r))
            for r in self.con.execute(f"SELECT {','.join(_POINT_COLS)} FROM data_points")
        }
        with transaction(self.con):
            for p in points:
                p.finalize()
                natural = _natural_key(p.to_row())
                if p.claim_id in existing or natural in existing_natural:
                    duplicate += 1
                    continue
                existing_natural.add(natural)
                row = p.to_row()
                if not row.get("created_at"):
                    row["created_at"] = _utcnow()
                cols = ",".join(_POINT_COLS)
                placeholders = ",".join(":" + c for c in _POINT_COLS)
                self.con.execute(
                    f"INSERT INTO data_points({cols}) VALUES ({placeholders})",
                    {c: row.get(c) for c in _POINT_COLS},
                )
                existing.add(p.claim_id)
                inserted.append(p.claim_id)
            self._refresh_registry()
        return {
            "added": len(inserted),
            "duplicate": duplicate,
            "claim_ids": inserted,
        }

    def _refresh_registry(self) -> None:
        """Recompute property_registry counts + a default canonical unit.

        Canonical unit = the most common non-empty unit for the property.
        Descriptions/quantity-kinds are left for a curator to fill in.
        """
        rows = self.con.execute(
            "SELECT property_norm, unit, COUNT(*) AS n "
            "FROM data_points WHERE verification_status != 'rejected' "
            "GROUP BY property_norm, unit"
        ).fetchall()
        agg: dict[str, dict] = {}
        for r in rows:
            pn = r["property_norm"]
            slot = agg.setdefault(pn, {"n": 0, "units": {}})
            slot["n"] += r["n"]
            u = (r["unit"] or "").strip()
            if u:
                slot["units"][u] = slot["units"].get(u, 0) + r["n"]
        for pn, slot in agg.items():
            canonical = ""
            if slot["units"]:
                canonical = max(slot["units"].items(), key=lambda kv: kv[1])[0]
            existing = self.con.execute(
                "SELECT description, quantity_kind, aliases_json "
                "FROM property_registry WHERE property_norm = ?",
                (pn,),
            ).fetchone()
            desc = existing["description"] if existing else ""
            qkind = existing["quantity_kind"] if existing else ""
            aliases = existing["aliases_json"] if existing else "[]"
            self.con.execute(
                "INSERT OR REPLACE INTO property_registry"
                "(property_norm, canonical_unit, quantity_kind, description, "
                "n_points, aliases_json) VALUES (?, ?, ?, ?, ?, ?)",
                (pn, canonical, qkind, desc, slot["n"], aliases),
            )

    def list_points(
        self,
        *,
        subject: str | None = None,
        property: str | None = None,
        status: str | None = None,
        limit: int = 0,
    ) -> list[dict]:
        from .models import normalize_key

        clauses: list[str] = []
        params: list[object] = []
        if subject:
            clauses.append("subject_norm = ?")
            params.append(normalize_key(subject))
        if property:
            clauses.append("property_norm = ?")
            params.append(normalize_key(property))
        if status:
            clauses.append("verification_status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM data_points" + where
            + " ORDER BY subject_norm, property_norm, value_num"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.con.execute(sql, params)]

    def get_point(self, claim_id: str) -> dict | None:
        r = self.con.execute(
            "SELECT * FROM data_points WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        return dict(r) if r else None

    def get_points(self, claim_ids: list[str]) -> list[dict]:
        if not claim_ids:
            return []
        ph = ",".join("?" * len(claim_ids))
        return [
            dict(r)
            for r in self.con.execute(
                f"SELECT * FROM data_points WHERE claim_id IN ({ph})", claim_ids
            )
        ]

    def reindex_norm_keys(self) -> dict:
        """Recompute ``subject_norm`` / ``property_norm`` over every stored point.

        The norm columns are derived at write time by ``normalize_key``. Points
        written by an older revision of that function keep the older spelling
        (``square-root-law`` where the current key is ``square root law``), and
        every lookup keyed on the norm column — the ``ArtifactSpec.subjects``
        filter, ``list_points``, the property registry — then silently misses
        them. Returns the counts changed per column.
        """
        subjects = 0
        properties = 0
        with transaction(self.con):
            rows = self.con.execute(
                "SELECT claim_id, subject, subject_norm, property, property_norm "
                "FROM data_points"
            ).fetchall()
            for r in rows:
                want_s = normalize_key(r["subject"])
                want_p = normalize_key(r["property"])
                if want_s == r["subject_norm"] and want_p == r["property_norm"]:
                    continue
                subjects += want_s != r["subject_norm"]
                properties += want_p != r["property_norm"]
                self.con.execute(
                    "UPDATE data_points SET subject_norm = ?, property_norm = ? "
                    "WHERE claim_id = ?",
                    (want_s, want_p, r["claim_id"]),
                )
            self._refresh_registry()
        return {
            "scanned": len(rows),
            "subject_norm_updated": subjects,
            "property_norm_updated": properties,
        }

    def relabel_point(
        self, claim_id: str, *, subject: str | None = None, property: str | None = None
    ) -> dict:
        """Rewrite a claim's ``subject`` / ``property`` label in place.

        The label is presentation — it becomes a table row/column heading — so
        rewriting it (ASCII ``sigma_D`` -> inline LaTeX ``$\\sigma_D$``) must
        not require re-extracting and re-verifying the number. Value,
        provenance, and verification fields are untouched.

        ``claim_id`` is deliberately NOT recomputed even though it hashes the
        norm keys: it is the row identity that ``data_artifact_claims`` and the
        committed ``.dataspec.json`` sidecars reference, and re-keying would
        orphan them.

        Derived norm keys ARE recomputed, and that is the consequential part.
        ``consolidate`` resolves an artifact's rows by matching
        ``normalize_key(spec subject)`` against ``subject_norm``, and its
        COLUMNS by matching ``normalize_key(spec property)`` against
        ``property_norm`` — NOT by ``claim_id``. Rewriting either detaches this
        row from every committed spec that names the old spelling, and
        preserving ``claim_id`` does not protect against it. Callers must check
        :meth:`artifacts_naming` for BOTH axes first; ``wikify data relabel``
        refuses without ``--force`` when any committed artifact would lose the
        row.
        """
        sets: list[str] = []
        params: list[object] = []
        if subject is not None:
            sets += ["subject = ?", "subject_norm = ?"]
            params += [subject, normalize_key(subject)]
        if property is not None:
            sets += ["property = ?", "property_norm = ?"]
            params += [property, normalize_key(property)]
        if not sets:
            return {}
        with transaction(self.con):
            self.con.execute(
                f"UPDATE data_points SET {', '.join(sets)} WHERE claim_id = ?",
                [*params, claim_id],
            )
            self._refresh_registry()
        row = self.get_point(claim_id) or {}
        return {
            "subject": row.get("subject", ""),
            "subject_norm": row.get("subject_norm", ""),
            "property": row.get("property", ""),
            "property_norm": row.get("property_norm", ""),
        }

    def artifacts_naming(
        self, *, subject_norm: str | None = None, property_norm: str | None = None
    ) -> list[dict]:
        """Committed artifacts whose spec filters on either normalized key.

        A relabel rewrites the norm key, and these are exactly the specs that
        would stop matching the row — silently dropping it from a page that is
        already published. BOTH axes matter: ``consolidate`` resolves rows via
        ``subject_norm`` and columns via ``property_norm``, and
        ``spec.properties`` is mandatory where ``spec.subjects`` is optional,
        so the property is the MORE likely way to orphan a row.
        """
        out: list[dict] = []
        for rec in self.list_artifacts():
            if rec.get("status") != "committed":
                continue
            try:
                spec = ArtifactSpec.from_json(rec["spec_json"])
            except (KeyError, ValueError):
                continue
            axes: list[str] = []
            if subject_norm is not None and any(
                normalize_key(x) == subject_norm for x in spec.subjects
            ):
                axes.append("subjects")
            if property_norm is not None and any(
                normalize_key(x) == property_norm for x in spec.properties
            ):
                axes.append("properties")
            if axes:
                out.append({
                    "artifact_id": spec.artifact_id,
                    "title": spec.title,
                    "filters_on": axes,
                })
        return out

    def set_status(self, claim_id: str, status: str) -> None:
        self.con.execute(
            "UPDATE data_points SET verification_status = ? WHERE claim_id = ?",
            (status, claim_id),
        )

    # --- properties / subjects ------------------------------------------

    def subjects(self) -> list[dict]:
        return [
            dict(r)
            for r in self.con.execute(
                "SELECT subject_norm, MAX(subject) AS subject, COUNT(*) AS n "
                "FROM data_points GROUP BY subject_norm ORDER BY n DESC"
            )
        ]

    def properties(self) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM property_registry ORDER BY n_points DESC"
        )]

    # --- property sweeps -------------------------------------------------

    def property_doc_stats(self, property_norm: str) -> dict:
        """Distinct source docs for a property, split by table-eligibility.

        ``docs_in_table`` = docs with a quote-verified claim (the rows a
        consolidated table can carry). ``docs_extracted`` = docs with any
        non-rejected claim (extraction attempted, verified or not).
        """
        in_table = self.con.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM data_points "
            "WHERE property_norm = ? AND verification_status = 'verified'",
            (property_norm,),
        ).fetchone()[0]
        extracted = self.con.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM data_points "
            "WHERE property_norm = ? AND verification_status != 'rejected'",
            (property_norm,),
        ).fetchone()[0]
        return {"docs_in_table": in_table, "docs_extracted": extracted}

    def record_property_sweep(
        self,
        *,
        property: str,
        property_norm: str,
        docs_mentioning: int,
        docs_extracted: int,
        docs_in_table: int,
        candidate_chunks: int,
        truncated: bool,
    ) -> None:
        """Persist the latest whole-corpus sweep bookkeeping for a property."""
        with transaction(self.con):
            self.con.execute(
                "INSERT OR REPLACE INTO property_sweeps"
                "(property_norm, property, docs_mentioning, docs_extracted, "
                "docs_in_table, candidate_chunks, truncated, last_sweep) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    property_norm, property, docs_mentioning, docs_extracted,
                    docs_in_table, candidate_chunks, 1 if truncated else 0,
                    _utcnow(),
                ),
            )

    def get_property_sweep(self, property_norm: str) -> dict | None:
        r = self.con.execute(
            "SELECT * FROM property_sweeps WHERE property_norm = ?",
            (property_norm,),
        ).fetchone()
        return dict(r) if r else None

    # --- artifacts -------------------------------------------------------

    def upsert_artifact(self, spec: ArtifactSpec, *, n_rows: int = 0) -> None:
        now = _utcnow()
        exists = self.con.execute(
            "SELECT created_at FROM data_artifacts WHERE artifact_id = ?",
            (spec.artifact_id,),
        ).fetchone()
        created = exists["created_at"] if exists else now
        keep_status = (
            "COALESCE((SELECT status FROM data_artifacts WHERE artifact_id = ?), 'draft')"
        )
        with transaction(self.con):
            self.con.execute(
                "INSERT OR REPLACE INTO data_artifacts"
                "(artifact_id, title, description, spec_json, status, n_rows, "
                f"created_at, updated_at) VALUES (?, ?, ?, ?, {keep_status}, ?, ?, ?)",
                (
                    spec.artifact_id, spec.title, spec.description, spec.to_json(),
                    spec.artifact_id, n_rows, created, now,
                ),
            )

    def set_artifact_status(self, artifact_id: str, status: str) -> None:
        self.con.execute(
            "UPDATE data_artifacts SET status = ?, updated_at = ? WHERE artifact_id = ?",
            (status, _utcnow(), artifact_id),
        )

    def set_artifact_claims(self, artifact_id: str, claim_ids: list[str]) -> None:
        with transaction(self.con):
            self.con.execute(
                "DELETE FROM data_artifact_claims WHERE artifact_id = ?",
                (artifact_id,),
            )
            self.con.executemany(
                "INSERT OR IGNORE INTO data_artifact_claims(artifact_id, claim_id) VALUES (?, ?)",
                [(artifact_id, cid) for cid in claim_ids],
            )

    def get_artifact(self, artifact_id: str) -> dict | None:
        r = self.con.execute(
            "SELECT * FROM data_artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return dict(r) if r else None

    def list_artifacts(self) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM data_artifacts ORDER BY updated_at DESC"
        )]

    def artifacts_for_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """Committed artifacts whose backing claims include any of *chunk_ids*.

        Lets a concept page discover the data artifact(s) that generalize the
        same sources, so the writer can link them instead of recreating a
        per-page table.
        """
        if not chunk_ids:
            return []
        ph = ",".join("?" * len(chunk_ids))
        rows = self.con.execute(
            "SELECT DISTINCT a.artifact_id, a.title, a.status FROM data_artifacts a "
            "JOIN data_artifact_claims ac ON ac.artifact_id = a.artifact_id "
            "JOIN data_points p ON p.claim_id = ac.claim_id "
            f"WHERE p.chunk_id IN ({ph}) AND a.status = 'committed' "
            "ORDER BY a.title",
            chunk_ids,
        )
        return [dict(r) for r in rows]

    def artifacts_for_docs(self, doc_ids: list[str]) -> list[dict]:
        """Committed artifacts whose backing claims include any of *doc_ids*.

        DOC-level counterpart to :meth:`artifacts_for_chunks`. The DATA wave
        harvests the number-dense chunks the article explorers skip, so a data
        artifact and the page it generalizes share source DOCUMENTS but not
        chunks -- a chunk intersection is empty by construction. Matching on
        the source document lets an artifact surface on its topical page.
        """
        if not doc_ids:
            return []
        ph = ",".join("?" * len(doc_ids))
        rows = self.con.execute(
            "SELECT DISTINCT a.artifact_id, a.title, a.status FROM data_artifacts a "
            "JOIN data_artifact_claims ac ON ac.artifact_id = a.artifact_id "
            "JOIN data_points p ON p.claim_id = ac.claim_id "
            f"WHERE p.doc_id IN ({ph}) AND a.status = 'committed' "
            "ORDER BY a.title",
            doc_ids,
        )
        return [dict(r) for r in rows]

    # --- summary ---------------------------------------------------------

    def coverage(self) -> dict:
        total = self.con.execute("SELECT COUNT(*) FROM data_points").fetchone()[0]
        verified = self.con.execute(
            "SELECT COUNT(*) FROM data_points WHERE verification_status = 'verified'"
        ).fetchone()[0]
        n_subjects = self.con.execute(
            "SELECT COUNT(DISTINCT subject_norm) FROM data_points"
        ).fetchone()[0]
        n_props = self.con.execute(
            "SELECT COUNT(DISTINCT property_norm) FROM data_points"
        ).fetchone()[0]
        n_docs = self.con.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM data_points"
        ).fetchone()[0]
        by_status = {
            r["verification_status"]: r["n"]
            for r in self.con.execute(
                "SELECT verification_status, COUNT(*) AS n FROM data_points "
                "GROUP BY verification_status"
            )
        }
        return {
            "n_points": total,
            "n_verified": verified,
            "verified_ratio": round(verified / total, 4) if total else 0.0,
            "n_subjects": n_subjects,
            "n_properties": n_props,
            "n_docs": n_docs,
            "n_artifacts": self.con.execute(
                "SELECT COUNT(*) FROM data_artifacts"
            ).fetchone()[0],
            "by_status": by_status,
        }
