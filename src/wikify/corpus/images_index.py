"""Per-corpus image index built from the SQLite ``assets`` table.

The corpus query store (``wikify.db``) is the source of truth for
figures / images / tables / schemes. ``ImageIndex.load(corpus)`` reads
the rows back and rebuilds caption-aware aliases so callers can
resolve free-form references like ``"Figure 1"`` or ``"fig 2a"`` to
the canonical image record.

Two lookup surfaces:

- ``ImageIndex.for_doc(doc_id)`` returns all images for one paper.
- ``ImageIndex.resolve(doc_id, label_or_id)`` returns the single image
  matching a free-form reference. Used by the wiki/extract path to
  attach figures to claims.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..api import Corpus

_LABEL_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _LABEL_NORM_RE.sub("_", s.lower()).strip("_")


# Match either "Figure 1" / "figure_01" / "Fig.2a" / "Table_3" — used to
# parse both caption labels AND filename stems.
_STEM_PARSE_RE = re.compile(
    r"^(?P<kind>fig(?:ure)?|table|scheme|sch)[._\s]*(?P<num>\d+)\s*(?P<sub>[a-z])?",
    re.IGNORECASE,
)


def _label_aliases(label: str | None, stem: str) -> list[str]:
    """Return all the strings that should resolve to this image.

    The stem (e.g. ``Figure_01``) is always one alias. We also try to
    parse a (kind, num, sub) triple from EITHER the caption label OR the
    stem itself — many figures don't have a caption-resolved label, but
    the stem still encodes the figure number, and we want chunks that
    say ``"Fig. 1"`` to resolve to ``Figure_01``.
    """
    out: set[str] = {_norm(stem)}
    triple: tuple[str, int, str] | None = None
    if label:
        m = _STEM_PARSE_RE.match(label.strip().lower())
        if m:
            kind = "figure" if m.group("kind").startswith("fig") else m.group("kind")
            triple = (kind, int(m.group("num")), (m.group("sub") or "").lower())
        out.add(_norm(label))
    if triple is None:
        m = _STEM_PARSE_RE.match(stem.strip().lower())
        if m:
            kind = "figure" if m.group("kind").startswith("fig") else m.group("kind")
            triple = (kind, int(m.group("num")), (m.group("sub") or "").lower())
    if triple is not None:
        kind, num, sub = triple
        for k in (kind, kind[:3]):
            for n in (str(num), f"{num:02d}"):
                out.add(_norm(f"{k} {n}{sub}"))
                out.add(_norm(f"{k}_{n}{sub}"))
    return sorted(out)


@dataclass(frozen=True)
class ImageRecord:
    id: str  # "<doc_id>/<stem>"
    label: str | None  # caption label (e.g. "Figure 1") or None
    caption: str
    alt_text: str
    page: int | None
    path: str  # relative to corpus root
    sidecar: str  # relative to corpus root
    media_type: str | None
    width: int | None
    height: int | None
    # Body chunks that reference this image via inline "Fig. N" / "Table N"
    # patterns. Populated by ``ingest.images.link_chunks_to_images`` at
    # refresh time. Empty list when no body discussion mentions the image.
    near_chunk_ids: tuple[str, ...] = ()


@dataclass
class ImageIndex:
    """Loaded view of the corpus's figure / table / scheme assets."""

    corpus_root: Path
    by_doc: dict[str, list[ImageRecord]] = field(default_factory=dict)
    by_alias: dict[str, str] = field(default_factory=dict)  # "<doc>/<norm>" -> id

    # ---- public API ------------------------------------------------------

    def for_doc(self, doc_id: str) -> list[ImageRecord]:
        return list(self.by_doc.get(doc_id, []))

    def resolve(self, doc_id: str, ref: str) -> ImageRecord | None:
        """Look up an image by free-form reference within a doc.

        ``ref`` can be a caption phrase (``"Figure 1"``, ``"fig 2a"``),
        a stem (``"Figure_01"``), or the fully-qualified id
        (``"<doc_id>/Figure_01"``).
        """
        if "/" in ref:
            for img in self.by_doc.get(doc_id, []):
                if img.id == ref:
                    return img
        key = f"{doc_id}/{_norm(ref)}"
        target_id = self.by_alias.get(key)
        if target_id is None:
            return None
        for img in self.by_doc.get(doc_id, []):
            if img.id == target_id:
                return img
        return None

    def all_records(self) -> list[ImageRecord]:
        out: list[ImageRecord] = []
        for recs in self.by_doc.values():
            out.extend(recs)
        return out

    # ---- persistence -----------------------------------------------------

    @classmethod
    def load(cls, corpus: Corpus) -> "ImageIndex":
        """Build the index from the corpus ``assets`` table."""
        return _load_from_sqlite(corpus)


def build_images_index(corpus: Corpus, doc_ids: list[str] | None = None) -> ImageIndex:
    """Return ``ImageIndex.load(corpus)``.

    ``doc_ids`` is accepted for backward compatibility with refresh-DAG
    callers; the SQLite-backed loader scopes itself by the rows present
    so the argument is now informational only.
    """
    del doc_ids
    return ImageIndex.load(corpus)


def _load_from_sqlite(corpus: Corpus) -> ImageIndex:
    if not corpus.sqlite_path.exists():
        return ImageIndex(corpus_root=corpus.root)
    by_doc: dict[str, list[ImageRecord]] = {}
    by_alias: dict[str, str] = {}
    con = sqlite3.connect(corpus.sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM assets "
            "WHERE asset_type IN ('figure','image','table','scheme') "
            "ORDER BY doc_id, asset_type, ord"
        ).fetchall()
        near_rows = con.execute(
            "SELECT ca.asset_id, ca.chunk_id, c.doc_id, c.ord "
            "FROM chunk_assets ca "
            "JOIN chunks c ON c.chunk_id = ca.chunk_id "
            "WHERE ca.relation = 'near' "
            "ORDER BY ca.asset_id, c.ord"
        ).fetchall()
    finally:
        con.close()

    near_by_asset: dict[str, list[str]] = {}
    for r in near_rows:
        near_by_asset.setdefault(str(r["asset_id"]), []).append(str(r["chunk_id"]))

    for r in rows:
        meta = _safe_json_obj(r["metadata_json"])
        bin_path = r["path"] or ""
        rel_bin = _relative_path(corpus.root, bin_path)
        rel_side = (
            rel_bin + ".json" if rel_bin else ""
        )
        asset_id = str(r["asset_id"])
        doc_id = str(r["doc_id"])
        stem = asset_id.rsplit("/", 1)[-1] if "/" in asset_id else asset_id
        rec = ImageRecord(
            id=asset_id,
            label=meta.get("label") or None,
            caption=str(r["caption"] or ""),
            alt_text=str(meta.get("alt_text") or ""),
            page=r["page"],
            path=rel_bin,
            sidecar=rel_side,
            media_type=str(r["asset_type"]) if r["asset_type"] else None,
            width=meta.get("width"),
            height=meta.get("height"),
            near_chunk_ids=tuple(near_by_asset.get(asset_id, ())),
        )
        by_doc.setdefault(doc_id, []).append(rec)
        for alias in _label_aliases(rec.label, stem):
            by_alias[f"{doc_id}/{alias}"] = asset_id

    return ImageIndex(corpus_root=corpus.root, by_doc=by_doc, by_alias=by_alias)


def _safe_json_obj(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _relative_path(root: Path, bin_path: str) -> str:
    if not bin_path:
        return ""
    p = Path(bin_path)
    try:
        rel = p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel = p
    return str(rel).replace("\\", "/")
