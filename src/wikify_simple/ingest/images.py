"""Image persistence for wikify_simple.

Parsers emit *raw* image dicts via ``ParseResult.metadata['_raw_images']``
and ``refresh.py`` calls ``save_doc_images`` to persist each image as:

    corpus/images/{paper_slug}/{label}.{ext}      # binary
    corpus/images/{paper_slug}/{label}.{ext}.json # sidecar

Where ``label`` is a caption-resolved name (``Fig_1``, ``Table_2``,
``Scheme_1a``) when the figure extractor matched a caption, falling back
to ``p{N}_img{i}`` when no caption could be resolved. The folder slug is a
clean truncation of the source filename (no hash suffix) so paths stay
under the Windows MAX_PATH limit.

Sidecars carry ``{id, caption, alt_text, page, near_chunk_ids,
source_bbox, ...}`` so they can be grepped or loaded back via
``read_doc_images``. The sidecar is the on-disk source of truth for the
post-ingest image inventory.

"""

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from ..models import Chunk, DocImage

__all__ = [
    "save_doc_images",
    "load_sidecars",
    "caption_chunks_for",
]

logger = logging.getLogger(__name__)


def save_doc_images(
    doc_id: str,
    image_dir: Path,
    raw_images: list[dict],
) -> list[DocImage]:
    """Persist raw parser image blobs + sidecar JSON; return DocImage records.

    Record shapes accepted:
      - ``{bytes, ext, page, caption, alt_text?, label?, bbox?, ...}`` (binary)
      - ``{url, caption?, alt_text?, page?}`` (url-only; html remote refs)
    """
    out: list[DocImage] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for i, rec in enumerate(raw_images or []):
        blob = rec.get("bytes")
        page = rec.get("page")
        caption = rec.get("caption", "") or ""
        alt_text = rec.get("alt_text", "") or ""
        bbox = rec.get("bbox")
        label = rec.get("label")
        if blob:
            ext = (rec.get("ext") or "png").lstrip(".")
            stem = _figure_stem(label, i)
            stem = _disambiguate(stem, used_names)
            used_names.add(stem)
            img_id = f"{doc_id}/{stem}"
            bin_path = image_dir / f"{stem}.{ext}"
            bin_path.write_bytes(blob)
            rel_path = str(bin_path)
            _write_sidecar(
                bin_path,
                {
                    "id": img_id,
                    "path": rel_path,
                    "caption": caption,
                    "alt_text": alt_text,
                    "page": page,
                    "near_chunk_ids": [],
                    "source_bbox": bbox,
                    "label": rec.get("label"),
                    "media_type": rec.get("media_type"),
                    "width": rec.get("width"),
                    "height": rec.get("height"),
                    "content_hash": rec.get("content_hash"),
                    "source_url": None,
                },
            )
            out.append(
                DocImage(
                    id=img_id,
                    path=rel_path,
                    caption=caption,
                    alt_text=alt_text,
                    page=page,
                )
            )
        else:
            url = rec.get("url") or rec.get("src") or ""
            if not url:
                continue
            stem = _figure_stem(label, i)
            stem = _disambiguate(stem, used_names)
            used_names.add(stem)
            img_id = f"{doc_id}/{stem}"
            side = image_dir / f"{stem}.url.json"
            payload = {
                "id": img_id,
                "path": url,
                "caption": caption,
                "alt_text": alt_text,
                "page": page,
                "near_chunk_ids": [],
                "source_bbox": None,
                "label": None,
                "media_type": "figure",
                "source_url": url,
            }
            side.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            out.append(
                DocImage(
                    id=img_id,
                    path=url,
                    caption=caption,
                    alt_text=alt_text,
                    page=page,
                )
            )
    return out


def _write_sidecar(bin_path: Path, payload: dict) -> None:
    side = bin_path.with_suffix(bin_path.suffix + ".json")
    side.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_LABEL_RE = re.compile(
    r"^\s*(?P<kind>fig(?:ure)?|table|scheme)\.?\s*(?P<num>\d+)\s*(?P<sub>[a-z])?",
    re.IGNORECASE,
)


def _figure_stem(label: str | None, index: int) -> str:
    """Build a clean human-readable filename stem from a caption label.

    Normalises ``"Fig. 1"`` / ``"figure 1a"`` / ``"Table 2"`` to
    ``Figure_01``, ``Figure_01a``, ``Table_02``. Falls back to
    ``fig_{index:03d}`` when no label is present and to a sanitised
    version of the raw label when the regex does not match (e.g. an
    unusual caption convention).
    """
    if label:
        m = _LABEL_RE.match(label)
        if m:
            kind = m.group("kind").lower()
            kind = "Figure" if kind.startswith("fig") else kind.capitalize()
            num = int(m.group("num"))
            sub = (m.group("sub") or "").lower()
            return f"{kind}_{num:02d}{sub}"
        safe = re.sub(r"[^\w.-]", "_", label)
        safe = re.sub(r"_+", "_", safe).strip("_.")
        if safe:
            return safe
    return f"fig_{index:03d}"


def _disambiguate(stem: str, used: set[str]) -> str:
    """Append a numeric suffix when two images would collide on stem."""
    if stem not in used:
        return stem
    i = 2
    while f"{stem}_{i}" in used:
        i += 1
    return f"{stem}_{i}"


def load_sidecars(image_dir: Path) -> list[DocImage]:
    """Load DocImage records from sidecar JSONs in ``image_dir``.

    The on-disk sidecar is authoritative: ``store/corpus.read_doc_images``
    uses this to round-trip images without going through the Document
    JSON index.
    """
    out: list[DocImage] = []
    if not image_dir.exists():
        return out
    for p in sorted(image_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            DocImage(
                id=data.get("id", p.stem),
                path=data.get("path", ""),
                caption=data.get("caption", "") or "",
                alt_text=data.get("alt_text", "") or "",
                page=data.get("page"),
                near_chunk_ids=data.get("near_chunk_ids", []) or [],
            )
        )
    return out


def caption_chunks_for(doc_id: str, images: Iterable[DocImage], ord_offset: int) -> list[Chunk]:
    """Wrap image captions as Chunks so the embedder/graph can index them."""
    out: list[Chunk] = []
    ord_ = ord_offset
    for im in images:
        text = (im.caption or "").strip() or (im.alt_text or "").strip()
        if not text:
            continue
        out.append(
            Chunk(
                id=f"{im.id}__caption",
                doc_id=doc_id,
                ord=ord_,
                text=text,
                char_span=(0, len(text)),
                section_path=["__image__", im.id],
            )
        )
        ord_ += 1
    return out
