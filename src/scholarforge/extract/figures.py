"""Figure extraction from PDFs with content-addressed storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from scholarforge.config import settings
from scholarforge.store.models import Figure


def extract_figures(pdf_path: str, paper_id: str) -> list[Figure]:
    """Extract images from a PDF and store them content-addressed.

    Returns Figure model instances (not yet persisted).
    """
    doc = fitz.open(pdf_path)
    figures: list[Figure] = []
    seen_hashes: set[str] = set()

    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            image_bytes = base_image["image"]
            img_hash = hashlib.sha256(image_bytes).hexdigest()

            # Skip duplicates within same document
            if img_hash in seen_hashes:
                continue
            seen_hashes.add(img_hash)

            # Skip tiny images (likely icons/decorations)
            width = base_image.get("width", 0)
            height = base_image.get("height", 0)
            if width < 50 or height < 50:
                continue

            ext = base_image.get("ext", "png")
            image_path = _store_figure(image_bytes, img_hash, ext)

            # Try to find caption near the image
            caption = _find_caption(page, page_num, img_index)

            figures.append(
                Figure(
                    id=img_hash,
                    paper_id=paper_id,
                    caption=caption,
                    figure_number=f"p{page_num + 1}_img{img_index}",
                    image_path=str(image_path),
                    width_px=width,
                    height_px=height,
                    format=ext,
                )
            )

    doc.close()
    return figures


def _store_figure(image_bytes: bytes, img_hash: str, ext: str) -> Path:
    """Store figure bytes in content-addressed directory."""
    subdir = settings.figures_dir / img_hash[:2] / img_hash[2:4]
    subdir.mkdir(parents=True, exist_ok=True)
    filepath = subdir / f"{img_hash}.{ext}"
    if not filepath.exists():
        filepath.write_bytes(image_bytes)
    return filepath


def _find_caption(page, page_num: int, img_index: int) -> str | None:
    """Attempt to find a figure caption near an image on the page.

    Simple heuristic: look for text blocks starting with 'Fig' or 'Figure'.
    """
    blocks = page.get_text("blocks")
    for block in blocks:
        text = block[4] if len(block) > 4 else ""
        if isinstance(text, str):
            stripped = text.strip()
            if stripped.lower().startswith(("fig.", "fig ", "figure")):
                return stripped[:500]  # Cap caption length
    return None
