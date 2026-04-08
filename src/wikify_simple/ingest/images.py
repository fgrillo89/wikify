"""Image persistence for wikify_simple.

Parsers emit *raw* image dicts via ``ParseResult.metadata['_raw_images']``
and ``refresh.py`` calls ``save_doc_images`` to persist each image as:

    corpus/images/{doc_id}/fig_{nnn}.{ext}        # binary
    corpus/images/{doc_id}/fig_{nnn}.{ext}.json   # sidecar

Sidecars carry ``{id, caption, alt_text, page, near_chunk_ids,
source_bbox, ...}`` so they can be grepped or loaded back via
``read_doc_images``. The sidecar is the on-disk source of truth for the
post-ingest image inventory.

The PDF-specific extraction (fitz-based caption matching, dedup, scan
detection) lives in ``ingest/figures.py`` and is re-exported here as
``extract_pdf_media`` for call sites that already use it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from ..models import Chunk, DocImage
from .figures import extract_pdf_media  # re-export

__all__ = [
    "extract_pdf_media",
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
    for i, rec in enumerate(raw_images or []):
        blob = rec.get("bytes")
        page = rec.get("page")
        caption = rec.get("caption", "") or ""
        alt_text = rec.get("alt_text", "") or ""
        bbox = rec.get("bbox")
        img_id = f"{doc_id}/fig_{i:03d}"
        if blob:
            ext = (rec.get("ext") or "png").lstrip(".")
            bin_path = image_dir / f"fig_{i:03d}.{ext}"
            try:
                bin_path.write_bytes(blob)
            except OSError:
                logger.debug("failed to write image %s", bin_path)
                continue
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
            side = image_dir / f"fig_{i:03d}.url.json"
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
            try:
                side.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError:
                continue
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
    try:
        side.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("failed to write sidecar %s", side)


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
