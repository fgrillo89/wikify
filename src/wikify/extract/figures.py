"""Figure extraction from PDFs with per-paper directory storage (legacy fallback)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import fitz

from wikify.config import settings
from wikify.store.models import Figure


def extract_figures(pdf_path: str, paper_id: str) -> list[Figure]:
    """Extract images from a PDF and store them in a per-paper directory.

    Returns Figure model instances (not yet persisted).
    """
    doc = fitz.open(pdf_path)
    figures: list[Figure] = []
    seen_hashes: set[str] = set()
    paper_slug = _make_paper_slug(pdf_path)

    max_figures_per_paper = 50

    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        for img_index, img_info in enumerate(image_list):
            if len(figures) >= max_figures_per_paper:
                doc.close()
                return figures

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

            # Skip tiny images (likely icons/decorations) and very small ones
            width = base_image.get("width", 0)
            height = base_image.get("height", 0)
            if width < 100 or height < 100:
                continue

            # Skip images that are too small in bytes (likely formatting artifacts)
            if len(image_bytes) < 2000:
                continue

            ext = base_image.get("ext", "png")
            fig_number = f"p{page_num + 1}_img{img_index}"
            image_path = _store_figure(image_bytes, img_hash, ext, paper_slug, fig_number)

            # Try to find caption near the image
            caption = _find_caption(page, page_num, img_index)

            figures.append(
                Figure(
                    id=img_hash,
                    paper_id=paper_id,
                    caption=caption,
                    figure_number=fig_number,
                    image_path=str(image_path),
                    width_px=width,
                    height_px=height,
                    format=ext,
                )
            )

    doc.close()
    return figures


def _make_paper_slug(pdf_path: str) -> str:
    """Derive a short, filesystem-safe folder name from a PDF filename."""
    stem = Path(pdf_path).stem
    slug = re.sub(r"[^\w\s-]", "", stem)
    slug = re.sub(r"[\s]+", "_", slug)
    slug = slug.strip("_")
    return slug[:80]


def _store_figure(
    image_bytes: bytes, img_hash: str, ext: str, paper_slug: str, fig_number: str
) -> Path:
    """Store figure bytes in a per-paper directory."""
    safe_name = re.sub(r"[^\w.-]", "_", fig_number)
    safe_name = re.sub(r"_+", "_", safe_name).strip("_")

    subdir = settings.figures_dir / paper_slug
    subdir.mkdir(parents=True, exist_ok=True)
    filepath = subdir / f"{safe_name}.{ext}"

    # Handle collisions with different content
    if filepath.exists():
        existing_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
        if existing_hash == img_hash:
            return filepath
        filepath = subdir / f"{safe_name}_{img_hash[:8]}.{ext}"

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
