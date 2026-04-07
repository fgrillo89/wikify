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

# Pages with more images than this are treated as scanned (OCR fragments)
_SCAN_THRESHOLD = 15

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


def _make_paper_slug(pdf_path: str) -> str:
    """Derive a short, filesystem-safe folder name from a PDF filename.

    Example: "Kim 2021 - 4K-memristor array.pdf" -> "Kim_2021_4K-memristor_array"
    """
    stem = Path(pdf_path).stem
    # Replace spaces and problematic chars, keep hyphens and alphanumerics
    slug = re.sub(r"[^\w\s-]", "", stem)
    slug = re.sub(r"[\s]+", "_", slug)
    slug = slug.strip("_")
    # Truncate to keep paths reasonable (max 80 chars)
    return slug[:80]


def _make_figure_filename(figure_number: str, ext: str) -> str:
    """Build a human-readable filename from a figure label.

    Examples:
        "Fig. 1"  -> "Fig_1.png"
        "Table 2" -> "Table_2.png"
        "p3_img0" -> "p3_img0.png"
    """
    safe = re.sub(r"[^\w.-]", "_", figure_number)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{safe}.{ext}"


def extract_media(pdf_path: str, paper_id: str, md_text: str) -> list[Figure]:
    """Extract all figures and tables from a PDF.

    Strategy:
    1. Use fitz page.get_images() for binary image extraction, stored
       per-paper in human-readable directories.
    2. Match captions from markdown text and page text blocks using
       page proximity.

    Files are stored as: figures_dir / {paper_slug} / {figure_label}.{ext}
    Content hashes remain the Figure.id for deduplication.

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
    paper_slug = _make_paper_slug(pdf_path)

    try:
        # Pre-extract all captions from markdown text, keyed by page estimate
        md_captions = _extract_captions_from_markdown(md_text)

        for page_num in range(len(doc)):
            if len(figures) >= _MAX_MEDIA_PER_PAPER:
                break

            page = doc[page_num]

            # Extract page-level captions from text blocks
            page_captions = _extract_captions_from_page(page)

            # Extract images (tables are already in the markdown via pymupdf4llm,
            # which produces higher-quality table output than find_tables())
            image_figures = _extract_images_from_page(
                doc, page, page_num, paper_id, paper_slug,
                seen_hashes, page_captions, md_captions
            )
            figures.extend(image_figures)
    finally:
        doc.close()

    return figures[:_MAX_MEDIA_PER_PAPER]


def _extract_images_from_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    paper_id: str,
    paper_slug: str,
    seen_hashes: set[str],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[Figure]:
    """Extract binary images from a single page."""
    figures: list[Figure] = []
    image_list = page.get_images(full=True)

    # Scan detection: if too many images on one page, it's likely scanned
    if len(image_list) > _SCAN_THRESHOLD:
        logger.debug(
            "Skipping page %d: %d images suggests scanned content",
            page_num,
            len(image_list),
        )
        return _extract_scanned_page_figures(
            doc, page, page_num, paper_id, paper_slug, seen_hashes, page_captions, md_captions
        )

    # Collect all valid images first so we can resolve caption conflicts
    extracted: list[tuple[int, str, bytes, int, int, str, list[float] | None]] = []
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

        bbox = _find_image_bbox(page, xref)
        extracted.append((img_index, img_hash, image_bytes, width, height, ext, bbox))

    # Match captions with consumption: each caption used at most once.
    # When multiple images compete for the same caption, the largest wins.
    # We do two passes: first find best caption per image, then resolve conflicts.
    pending_matches: list[
        tuple[int, str, int, int, str, list[float] | None, _CaptionMatch | None]
    ] = []
    # Work on copies so consumption is local to this page's image extraction
    avail_page = list(page_captions)

    for img_index, img_hash, _img_bytes, width, height, ext, bbox in extracted:
        caption_match = _match_caption(
            page_num, bbox, avail_page, md_captions, exclude_type="table"
        )
        pending_matches.append((img_index, img_hash, width, height, ext, bbox, caption_match))

    # Resolve duplicate caption assignments: group by caption label,
    # keep only the largest image (by pixel area), others get no caption.
    label_winners: dict[str, int] = {}  # label -> index in pending_matches with max area
    for idx, (img_index, _h, w, h, _e, _b, cap) in enumerate(pending_matches):
        if cap is None:
            continue
        lbl = cap.label
        area = w * h
        if lbl not in label_winners:
            label_winners[lbl] = idx
        else:
            prev_idx = label_winners[lbl]
            prev_w = pending_matches[prev_idx][2]
            prev_h = pending_matches[prev_idx][3]
            if area > prev_w * prev_h:
                label_winners[lbl] = idx

    # Build figures, consuming captions for winners only
    consumed_labels: set[str] = set()
    for idx, (img_index, img_hash, width, height, ext, bbox, caption_match) in enumerate(
        pending_matches
    ):
        # Only the winner for each label gets the caption
        if caption_match is not None:
            lbl = caption_match.label
            if lbl in consumed_labels or label_winners.get(lbl) != idx:
                caption_match = None
            else:
                consumed_labels.add(lbl)
                # Remove consumed caption from page and md lists
                if caption_match in avail_page:
                    avail_page.remove(caption_match)
                _consume_md_caption(md_captions, lbl)

        caption = caption_match.text if caption_match else None
        label = caption_match.label if caption_match else None
        media_type = caption_match.media_type if caption_match else "figure"

        fig_number = label or f"p{page_num + 1}_img{img_index}"
        fig_filename = _make_figure_filename(fig_number, ext)
        img_path = _store_media(
            extracted[idx][2], img_hash, ext, paper_slug, fig_filename
        )
        _write_meta_sidecar(img_path, paper_id, page_num, width, height, ext)

        figures.append(
            Figure(
                id=img_hash,
                paper_id=paper_id,
                caption=caption,
                figure_number=fig_number,
                image_path=str(img_path),
                width_px=width,
                height_px=height,
                format=ext,
                media_type=media_type,
                label=label,
                page_number=page_num,
                bbox=json.dumps(bbox) if bbox else None,
            )
        )

    # Propagate page_captions consumption back to caller's list
    page_captions[:] = avail_page

    return figures


# ── Scanned page handling ─────────────────────────────────────────────────────


def _extract_scanned_page_figures(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    paper_id: str,
    paper_slug: str,
    seen_hashes: set[str],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[Figure]:
    """Handle a scanned page by rendering the full page as an image.

    Only produces figures if there are unmatched captions on the page.
    Each caption gets a copy of the full-page render.
    """
    # Filter to figure-type captions (not table) on this page
    figure_captions = [c for c in page_captions if c.media_type != "table"]
    if not figure_captions:
        return []

    # Render the full page as PNG
    try:
        pixmap = page.get_pixmap(dpi=150)
        page_bytes = pixmap.tobytes("png")
    except Exception:
        logger.debug("Failed to render scanned page %d as pixmap", page_num)
        return []

    figures: list[Figure] = []
    for cap in figure_captions:
        img_hash = hashlib.sha256(
            (paper_id + str(page_num) + cap.label).encode("utf-8") + page_bytes
        ).hexdigest()

        if img_hash in seen_hashes:
            continue
        seen_hashes.add(img_hash)

        fig_filename = _make_figure_filename(cap.label, "png")
        image_path = _store_media(page_bytes, img_hash, "png", paper_slug, fig_filename)
        width = pixmap.width
        height = pixmap.height
        _write_meta_sidecar(image_path, paper_id, page_num, width, height, "png")

        figures.append(
            Figure(
                id=img_hash,
                paper_id=paper_id,
                caption=cap.text,
                figure_number=cap.label,
                image_path=str(image_path),
                width_px=width,
                height_px=height,
                format="png",
                media_type=cap.media_type,
                label=cap.label,
                page_number=page_num,
            )
        )

        # Consume the caption from both page and md lists
        if cap in page_captions:
            page_captions.remove(cap)
        _consume_md_caption(md_captions, cap.label)

    return figures


def _consume_md_caption(md_captions: list[_CaptionMatch], label: str) -> None:
    """Remove the first markdown caption matching *label* (consumed after use)."""
    for i, mc in enumerate(md_captions):
        if mc.label == label:
            md_captions.pop(i)
            return


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


def _store_media(
    image_bytes: bytes,
    content_hash: str,
    ext: str,
    paper_slug: str = "",
    figure_filename: str = "",
) -> Path:
    """Store media bytes in a per-paper directory with a readable filename.

    Path: figures_dir / {paper_slug} / {figure_filename}
    Falls back to content-addressed layout if no slug/filename provided.
    Deduplication: if file already exists with same content hash, skip write.
    """
    if paper_slug and figure_filename:
        subdir = settings.figures_dir / paper_slug
        subdir.mkdir(parents=True, exist_ok=True)
        filepath = subdir / figure_filename
        # Handle filename collisions (e.g. two images both labeled "p3_img0")
        if filepath.exists():
            existing_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
            if existing_hash == content_hash:
                return filepath
            # Collision with different content: append short hash
            stem = filepath.stem
            filepath = subdir / f"{stem}_{content_hash[:8]}.{ext}"
    else:
        # Legacy fallback: content-addressed
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
