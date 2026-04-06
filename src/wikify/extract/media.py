"""Unified figure and table extraction from PDFs.

Replaces the image-only extraction in figures.py with a pipeline that
handles figures, tables, schemes, and charts. Produces Figure model
instances with media_type, label, page_number, and bbox populated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import fitz

from wikify.config import settings
from wikify.store.models import Figure

logger = logging.getLogger(__name__)

# Maximum media items to extract per paper (guard against pathological PDFs)
_MAX_MEDIA_PER_PAPER = 80

# Minimum image dimensions / bytes to skip icons and decorations
_MIN_WIDTH = 100
_MIN_HEIGHT = 100
_MIN_BYTES = 2000

# Caption patterns (case-insensitive)
_FIGURE_CAPTION_RE = re.compile(
    r"(?i)(fig(?:ure)?\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL
)
_TABLE_CAPTION_RE = re.compile(r"(?i)(table\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)
_SCHEME_CAPTION_RE = re.compile(r"(?i)(scheme\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)
_CHART_CAPTION_RE = re.compile(r"(?i)(chart\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)

# Maps pattern -> media_type
_CAPTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_FIGURE_CAPTION_RE, "figure"),
    (_TABLE_CAPTION_RE, "table"),
    (_SCHEME_CAPTION_RE, "scheme"),
    (_CHART_CAPTION_RE, "chart"),
]


def extract_media(pdf_path: str, paper_id: str, md_text: str) -> list[Figure]:
    """Extract all figures and tables from a PDF.

    Strategy:
    1. Use fitz page.get_images() for binary image extraction with
       content-addressed storage.
    2. Use fitz page.find_tables() for structured table extraction.
    3. Match captions from markdown text and page text blocks using
       page proximity.

    Args:
        pdf_path: Path to the PDF file.
        paper_id: Stable paper identifier (SHA256 of file content).
        md_text: Full markdown text of the paper (from pymupdf4llm).

    Returns:
        List of Figure model instances (not yet persisted).
    """
    doc = fitz.open(pdf_path)
    figures: list[Figure] = []
    seen_hashes: set[str] = set()

    try:
        # Pre-extract all captions from markdown text, keyed by page estimate
        md_captions = _extract_captions_from_markdown(md_text)

        for page_num in range(len(doc)):
            if len(figures) >= _MAX_MEDIA_PER_PAPER:
                break

            page = doc[page_num]

            # Extract page-level captions from text blocks
            page_captions = _extract_captions_from_page(page)

            # 1. Extract images
            image_figures = _extract_images_from_page(
                doc, page, page_num, paper_id, seen_hashes, page_captions, md_captions
            )
            figures.extend(image_figures)

            # 2. Extract tables
            table_figures = _extract_tables_from_page(
                page, page_num, paper_id, seen_hashes, page_captions, md_captions
            )
            figures.extend(table_figures)
    finally:
        doc.close()

    return figures[:_MAX_MEDIA_PER_PAPER]


def _extract_images_from_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    paper_id: str,
    seen_hashes: set[str],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[Figure]:
    """Extract binary images from a single page."""
    figures: list[Figure] = []
    image_list = page.get_images(full=True)

    for img_index, img_info in enumerate(image_list):
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
        except Exception:
            logger.debug("Failed to extract image xref=%d on page %d", xref, page_num)
            continue

        image_bytes = base_image["image"]
        width = base_image.get("width", 0)
        height = base_image.get("height", 0)
        ext = base_image.get("ext", "png")

        # Skip tiny/decoration images
        if width < _MIN_WIDTH or height < _MIN_HEIGHT:
            continue
        if len(image_bytes) < _MIN_BYTES:
            continue

        img_hash = hashlib.sha256(image_bytes).hexdigest()
        if img_hash in seen_hashes:
            continue
        seen_hashes.add(img_hash)

        image_path = _store_media(image_bytes, img_hash, ext)
        _write_meta_sidecar(image_path, paper_id, page_num, width, height, ext)

        # Try to find the image's bounding box on the page
        bbox = _find_image_bbox(page, xref)

        # Match caption
        caption_match = _match_caption(
            page_num, bbox, page_captions, md_captions, exclude_type="table"
        )
        caption = caption_match.text if caption_match else None
        label = caption_match.label if caption_match else None
        media_type = caption_match.media_type if caption_match else "figure"

        figures.append(
            Figure(
                id=img_hash,
                paper_id=paper_id,
                caption=caption,
                figure_number=label or f"p{page_num + 1}_img{img_index}",
                image_path=str(image_path),
                width_px=width,
                height_px=height,
                format=ext,
                media_type=media_type,
                label=label,
                page_number=page_num,
                bbox=json.dumps(bbox) if bbox else None,
            )
        )

    return figures


def _extract_tables_from_page(
    page: fitz.Page,
    page_num: int,
    paper_id: str,
    seen_hashes: set[str],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[Figure]:
    """Extract structured tables from a single page."""
    figures: list[Figure] = []

    try:
        tables = page.find_tables(strategy="lines_strict")
    except Exception:
        logger.debug("Table extraction failed on page %d", page_num)
        return figures

    for tbl_index, table in enumerate(tables):
        try:
            # Extract table data as list of lists
            data = table.extract()
        except Exception:
            logger.debug(
                "Failed to extract table data on page %d, table %d",
                page_num,
                tbl_index,
            )
            continue

        if not data or len(data) < 2:
            continue  # Skip trivially small tables

        # Build markdown representation
        md_table = _table_data_to_markdown(data)
        if not md_table:
            continue

        # Content-address the table by its markdown content
        table_hash = hashlib.sha256((paper_id + md_table).encode("utf-8")).hexdigest()

        if table_hash in seen_hashes:
            continue
        seen_hashes.add(table_hash)

        bbox_rect = table.bbox if hasattr(table, "bbox") else None
        bbox = list(bbox_rect) if bbox_rect else None

        # Match caption
        caption_match = _match_caption(
            page_num, bbox, page_captions, md_captions, prefer_type="table"
        )
        caption = caption_match.text if caption_match else None
        label = caption_match.label if caption_match else None

        figures.append(
            Figure(
                id=table_hash,
                paper_id=paper_id,
                caption=caption,
                figure_number=label or f"p{page_num + 1}_tbl{tbl_index}",
                media_type="table",
                label=label,
                page_number=page_num,
                bbox=json.dumps(bbox) if bbox else None,
                markdown_table=md_table,
                extracted_data=json.dumps(data),
            )
        )

    return figures


# ── Caption extraction and matching ────────────────────────────────────────────


class _CaptionMatch:
    """A matched caption with metadata for proximity matching."""

    __slots__ = ("label", "text", "media_type", "page_num", "y_position")

    def __init__(
        self,
        label: str,
        text: str,
        media_type: str,
        page_num: int | None = None,
        y_position: float | None = None,
    ):
        self.label = label
        self.text = text[:500]  # Cap caption length
        self.media_type = media_type
        self.page_num = page_num
        self.y_position = y_position


def _extract_captions_from_page(page: fitz.Page) -> list[_CaptionMatch]:
    """Extract caption text blocks from a PDF page using text blocks."""
    captions: list[_CaptionMatch] = []
    blocks = page.get_text("blocks")

    for block in blocks:
        if len(block) < 5:
            continue
        text = block[4]
        if not isinstance(text, str):
            continue

        stripped = text.strip()
        y_pos = block[1]  # y0 of the text block

        for pattern, media_type in _CAPTION_PATTERNS:
            match = pattern.match(stripped)
            if match:
                label = match.group(1).strip()
                caption_text = match.group(2).strip()
                captions.append(
                    _CaptionMatch(
                        label=label,
                        text=caption_text if caption_text else stripped,
                        media_type=media_type,
                        y_position=y_pos,
                    )
                )
                break

    return captions


def _extract_captions_from_markdown(md_text: str) -> list[_CaptionMatch]:
    """Extract captions from the full markdown text (no page numbers)."""
    captions: list[_CaptionMatch] = []

    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Remove bold markers for matching
        cleaned = stripped.replace("**", "")

        for pattern, media_type in _CAPTION_PATTERNS:
            match = pattern.match(cleaned)
            if match:
                label = match.group(1).strip()
                caption_text = match.group(2).strip()
                captions.append(
                    _CaptionMatch(
                        label=label,
                        text=caption_text if caption_text else cleaned,
                        media_type=media_type,
                    )
                )
                break

    return captions


def _match_caption(
    page_num: int,
    bbox: list[float] | None,
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
    exclude_type: str | None = None,
    prefer_type: str | None = None,
) -> _CaptionMatch | None:
    """Match the best caption to a media item using proximity.

    For images, exclude_type="table" avoids matching table captions.
    For tables, prefer_type="table" prioritizes table captions.
    """
    # Filter page captions
    candidates = page_captions
    if exclude_type:
        candidates = [c for c in candidates if c.media_type != exclude_type]

    if prefer_type:
        preferred = [c for c in candidates if c.media_type == prefer_type]
        if preferred:
            candidates = preferred

    if not candidates:
        # Fall back to markdown captions (no positional info)
        md_candidates = md_captions
        if exclude_type:
            md_candidates = [c for c in md_candidates if c.media_type != exclude_type]
        if prefer_type:
            preferred = [c for c in md_candidates if c.media_type == prefer_type]
            if preferred:
                md_candidates = preferred
        return md_candidates[0] if md_candidates else None

    # If we have bbox, pick the caption closest vertically (below the figure)
    if bbox and len(bbox) >= 4:
        fig_bottom = bbox[3]  # y1 of the figure
        best = None
        best_dist = float("inf")
        for cap in candidates:
            if cap.y_position is not None:
                # Prefer captions below the figure
                dist = abs(cap.y_position - fig_bottom)
                if dist < best_dist:
                    best_dist = dist
                    best = cap
        if best:
            return best

    # No bbox or no positioned captions: return first candidate
    return candidates[0] if candidates else None


# ── Storage helpers ────────────────────────────────────────────────────────────


def _store_media(image_bytes: bytes, content_hash: str, ext: str) -> Path:
    """Store media bytes in content-addressed directory.

    Path: figures_dir / hash[:2] / hash[2:4] / {hash}.{ext}
    """
    subdir = settings.figures_dir / content_hash[:2] / content_hash[2:4]
    subdir.mkdir(parents=True, exist_ok=True)
    filepath = subdir / f"{content_hash}.{ext}"
    if not filepath.exists():
        filepath.write_bytes(image_bytes)
    return filepath


def _write_meta_sidecar(
    image_path: Path,
    paper_id: str,
    page_num: int,
    width: int,
    height: int,
    ext: str,
) -> None:
    """Write a .meta.json sidecar file alongside the image for quick inspection."""
    meta_path = image_path.with_suffix(f".{ext}.meta.json")
    meta = {
        "paper_id": paper_id,
        "page_number": page_num,
        "width_px": width,
        "height_px": height,
        "format": ext,
    }
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("Failed to write sidecar for %s", image_path)


def _find_image_bbox(page: fitz.Page, xref: int) -> list[float] | None:
    """Try to find the bounding box of an image on the page by its xref."""
    try:
        for img in page.get_image_info(xrefs=True):
            if img.get("xref") == xref:
                bbox = img.get("bbox")
                if bbox:
                    return list(bbox)
    except Exception:
        pass
    return None


def _table_data_to_markdown(data: list[list]) -> str:
    """Convert a list-of-lists table to a markdown table string.

    Args:
        data: List of rows, each row is a list of cell strings.

    Returns:
        Markdown table string, or empty string if data is invalid.
    """
    if not data or not data[0]:
        return ""

    ncols = max(len(row) for row in data)
    rows: list[str] = []

    for i, row in enumerate(data):
        # Pad short rows
        cells = [(c or "").replace("|", "\\|").replace("\n", " ") for c in row]
        cells.extend([""] * (ncols - len(cells)))
        rows.append("| " + " | ".join(cells) + " |")

        # Add separator after header row
        if i == 0:
            rows.append("| " + " | ".join(["---"] * ncols) + " |")

    return "\n".join(rows)
