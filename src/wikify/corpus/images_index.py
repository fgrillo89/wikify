"""Per-corpus image index — the model-facing surface for figure lookup.

The index is a single ``corpus/images.json`` file built once at ingest
time and rebuildable at any moment from the on-disk sidecar JSONs (see
``ingest/images.py::load_sidecars``). It is a *projection*, not a source
of truth: deleting it and calling ``rebuild_images_index`` reconstructs
the same content from the sidecars.

Shape::

    {
      "version": 1,
      "by_doc": {
        "<doc_id>": [
          {
            "id":       "<doc_id>/Figure_01",
            "label":    "Figure 1",       # caption-resolved when present
            "caption":  "Schematic of …",
            "alt_text": "",
            "page":     2,
            "path":     "images/<slug>/Figure_01.png",   # relative to corpus root
            "sidecar":  "images/<slug>/Figure_01.png.json",
            "media_type": "figure",
            "width":  962,
            "height": 720
          },
          ...
        ]
      },
      "by_label": {
        "<doc_id>/figure_1":  "<doc_id>/Figure_01",
        "<doc_id>/figure_01": "<doc_id>/Figure_01",
        "<doc_id>/fig_1":     "<doc_id>/Figure_01",
        ...
      }
    }

Two lookup surfaces:

- ``ImageIndex.for_doc(doc_id)`` returns all images for one paper.
- ``ImageIndex.resolve(doc_id, label_or_id)`` returns the single image
  matching a free-form reference like ``"Figure 1"``, ``"fig 1a"``,
  ``"Figure_01"``, or the full ``id``. Used by the wiki/extract path
  to attach figures to claims.

The wikification pipeline reads the index once via
``ImageIndex.load(corpus)`` and uses it as the canonical figure
catalogue.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import CorpusPaths

_LABEL_NORM_RE = re.compile(r"[^a-z0-9]+")
_LABEL_PARSE_RE = re.compile(
    r"^(?P<kind>fig(?:ure)?|table|scheme)\.?\s*(?P<num>\d+)\s*(?P<sub>[a-z])?$"
)


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
    """Loaded view of ``corpus/images.json``."""

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
    def load(cls, corpus: CorpusPaths) -> "ImageIndex":
        path = corpus.images_index_path
        if not path.exists():
            return cls(corpus_root=corpus.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        by_doc: dict[str, list[ImageRecord]] = {}
        by_alias: dict[str, str] = {}
        for doc_id, recs in data.get("by_doc", {}).items():
            by_doc[doc_id] = [
                ImageRecord(
                    id=r["id"],
                    label=r.get("label"),
                    caption=r.get("caption", "") or "",
                    alt_text=r.get("alt_text", "") or "",
                    page=r.get("page"),
                    path=r["path"],
                    sidecar=r["sidecar"],
                    media_type=r.get("media_type"),
                    width=r.get("width"),
                    height=r.get("height"),
                    near_chunk_ids=tuple(r.get("near_chunk_ids") or ()),
                )
                for r in recs
            ]
        by_alias = dict(data.get("by_alias", {}))
        return cls(corpus_root=corpus.root, by_doc=by_doc, by_alias=by_alias)


def build_images_index(corpus: CorpusPaths, doc_ids: list[str]) -> ImageIndex:
    """Build the index from already-written sidecar JSONs.

    Called at the end of ``ingest_corpus``. Walks each doc's image
    folder via ``load_sidecars`` and assembles the projection.
    """
    by_doc: dict[str, list[ImageRecord]] = {}
    by_alias: dict[str, str] = {}
    images_dir = corpus.images_dir
    if not images_dir.exists():
        return ImageIndex(corpus_root=corpus.root)
    for folder in sorted(images_dir.iterdir()):
        if not folder.is_dir():
            continue
        records = _records_for_folder(corpus, folder)
        if not records:
            continue
        # records are tagged with the doc_id read from the sidecar's id field
        for doc_id, rec in records:
            by_doc.setdefault(doc_id, []).append(rec)
            stem = rec.id.rsplit("/", 1)[-1]
            for alias in _label_aliases(rec.label, stem):
                by_alias[f"{doc_id}/{alias}"] = rec.id
    idx = ImageIndex(corpus_root=corpus.root, by_doc=by_doc, by_alias=by_alias)
    save_images_index(corpus, idx)
    return idx


def rebuild_images_index(corpus: CorpusPaths) -> ImageIndex:
    """Reconstruct the index by walking every image folder on disk."""
    return build_images_index(corpus, doc_ids=[])


def save_images_index(corpus: CorpusPaths, idx: ImageIndex) -> Path:
    payload = {
        "version": 1,
        "by_doc": {
            doc_id: [
                {
                    "id": r.id,
                    "label": r.label,
                    "caption": r.caption,
                    "alt_text": r.alt_text,
                    "page": r.page,
                    "path": r.path,
                    "sidecar": r.sidecar,
                    "media_type": r.media_type,
                    "width": r.width,
                    "height": r.height,
                    "near_chunk_ids": list(r.near_chunk_ids),
                }
                for r in recs
            ]
            for doc_id, recs in idx.by_doc.items()
        },
        "by_alias": idx.by_alias,
    }
    return _atomic_write(corpus.images_index_path, json.dumps(payload, indent=2))


# ---- internal -----------------------------------------------------------


def _records_for_folder(corpus: CorpusPaths, folder: Path) -> list[tuple[str, ImageRecord]]:
    """Read the sidecar JSONs in ``folder`` and return ``(doc_id, ImageRecord)``."""
    out: list[tuple[str, ImageRecord]] = []
    sidecar_files = sorted(folder.glob("*.json"))
    root = corpus.root
    for p in sidecar_files:
        try:
            side = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "id" not in side:
            continue
        doc_id, _, _ = side["id"].partition("/")
        bin_abs = Path(side.get("path") or "")
        try:
            rel_bin = bin_abs.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            rel_bin = bin_abs
        rel_side = rel_bin.with_suffix(rel_bin.suffix + ".json")
        rec = ImageRecord(
            id=side["id"],
            label=side.get("label"),
            caption=side.get("caption", "") or "",
            alt_text=side.get("alt_text", "") or "",
            page=side.get("page"),
            path=str(rel_bin).replace("\\", "/"),
            sidecar=str(rel_side).replace("\\", "/"),
            media_type=side.get("media_type"),
            width=side.get("width"),
            height=side.get("height"),
            near_chunk_ids=tuple(side.get("near_chunk_ids") or ()),
        )
        out.append((doc_id, rec))
    return out


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".idx-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
