"""Image-as-first-class-unit helpers.

In wikify_simple, every DocImage carries caption + alt text + near_chunk_ids.
Captions are embedded by ingest/embedder.py alongside text chunks so the
sampler can treat them uniformly (see strategies.md "Images as first-class
units"). The markdown parser does not yet emit images; the pdf/docx/pptx
ports will populate this.

This module currently exposes one helper: ``caption_chunks_for`` builds
synthetic Chunk-like records that the embedder consumes for image captions.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..models import Chunk, DocImage


def save_doc_images(
    doc_id: str,
    image_dir: Path,
    raw_images: list[dict],
) -> list[DocImage]:
    """Persist raw parser image blobs to ``image_dir`` and return DocImage records.

    Each record in ``raw_images`` is a dict ``{bytes, ext, page, caption, alt_text?}``
    as emitted by the pdf/docx/pptx parsers. URL-only records (no bytes) are
    passed through as DocImage with ``path`` set to the URL.
    """
    out: list[DocImage] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    for i, rec in enumerate(raw_images or []):
        blob = rec.get("bytes")
        ext = (rec.get("ext") or "png").lstrip(".")
        page = rec.get("page")
        caption = rec.get("caption", "") or ""
        alt_text = rec.get("alt_text", "") or ""
        img_id = f"{doc_id}/fig_{i:03d}"
        if blob:
            path = image_dir / f"fig_{i:03d}.{ext}"
            try:
                path.write_bytes(blob)
            except Exception:
                continue
            out.append(
                DocImage(
                    id=img_id,
                    path=str(path),
                    caption=caption,
                    alt_text=alt_text,
                    page=page,
                )
            )
        else:
            # URL-only (html); just record the URL
            url = rec.get("url") or rec.get("src") or ""
            if not url:
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


def caption_chunks_for(doc_id: str, images: Iterable[DocImage], ord_offset: int) -> list[Chunk]:
    """Wrap image captions as Chunks so the embedder/graph can index them.

    The chunk id is ``{image.id}:caption`` so the sampler can detect image
    chunks by suffix without a separate code path.
    """
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
