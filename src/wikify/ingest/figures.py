"""PDF figure/media extraction.

Caption-matching, dedup, scan detection, and bbox logic. Returns typed
``RawImage`` records that ``ingest/images.py::save_doc_images`` writes to
disk alongside a JSON sidecar.
"""

import hashlib
import re

from .config import (
    MAX_MEDIA_PER_PAPER,
    MIN_IMG_BYTES,
    MIN_IMG_HEIGHT,
    MIN_IMG_WIDTH,
    SCAN_THRESHOLD,
)
from .parsers.registry import RawImage

_FIGURE_CAPTION_RE = re.compile(
    r"(?i)(fig(?:ure)?\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL
)
_TABLE_CAPTION_RE = re.compile(r"(?i)(table\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)
_SCHEME_CAPTION_RE = re.compile(r"(?i)(scheme\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)
_CHART_CAPTION_RE = re.compile(r"(?i)(chart\.?\s*\d+[a-z]?)\s*[.:\s\u2014\-]+(.*)", re.DOTALL)

_CAPTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_FIGURE_CAPTION_RE, "figure"),
    (_TABLE_CAPTION_RE, "table"),
    (_SCHEME_CAPTION_RE, "scheme"),
    (_CHART_CAPTION_RE, "chart"),
]


class _CaptionMatch:
    __slots__ = ("label", "text", "media_type", "y_position")

    def __init__(
        self,
        label: str,
        text: str,
        media_type: str,
        y_position: float | None = None,
    ):
        self.label = label
        self.text = text[:500]
        self.media_type = media_type
        self.y_position = y_position


def extract_pdf_media(doc, md_text: str) -> list[RawImage]:
    """Return typed RawImage records extracted from an open fitz Document.

    Dedup is by content sha256.
    """
    raw: list[dict] = []
    seen: set[str] = set()
    md_captions = _extract_captions_from_markdown(md_text)

    try:
        n_pages = doc.page_count
    except Exception:
        return raw

    for page_num in range(n_pages):
        if len(raw) >= MAX_MEDIA_PER_PAPER:
            break
        try:
            page = doc[page_num]
            image_list = page.get_images(full=True)
        except Exception:
            continue

        page_captions = _extract_captions_from_page(page)

        if len(image_list) > SCAN_THRESHOLD:
            raw.extend(_scanned_page_raw(page, page_num, seen, page_captions, md_captions))
            continue

        extracted = _extract_images_on_page(doc, page, image_list, seen)
        raw.extend(_build_records(page_num, extracted, page_captions, md_captions))

    return [_dict_to_raw_image(d) for d in raw[:MAX_MEDIA_PER_PAPER]]


def _extract_images_on_page(
    doc, page, image_list, seen: set[str]
) -> list[tuple[int, str, bytes, int, int, str, list[float] | None]]:
    out: list[tuple[int, str, bytes, int, int, str, list[float] | None]] = []
    for img_index, img_info in enumerate(image_list):
        xref = img_info[0]
        try:
            base = doc.extract_image(xref)
        except Exception:
            continue
        blob = base["image"]
        width = base.get("width", 0)
        height = base.get("height", 0)
        ext = base.get("ext", "png")
        if width < MIN_IMG_WIDTH or height < MIN_IMG_HEIGHT:
            continue
        if len(blob) < MIN_IMG_BYTES:
            continue
        h = hashlib.sha256(blob).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        bbox = _find_image_bbox(page, xref)
        out.append((img_index, h, blob, width, height, ext, bbox))
    return out


def _build_records(
    page_num: int,
    extracted: list[tuple[int, str, bytes, int, int, str, list[float] | None]],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[dict]:
    avail = list(page_captions)
    pending: list[dict] = []
    for img_index, h, blob, w, ht, ext, bbox in extracted:
        cap = _match_caption(bbox, avail, md_captions, exclude_type="table")
        pending.append(
            {
                "bytes": blob,
                "ext": ext,
                "page": page_num + 1,
                "width": w,
                "height": ht,
                "content_hash": h,
                "bbox": bbox,
                "_cap": cap,
                "_img_index": img_index,
            }
        )

    # Largest-image-wins per caption label.
    label_winner: dict[str, int] = {}
    for idx, rec in enumerate(pending):
        cap = rec["_cap"]
        if cap is None:
            continue
        area = rec["width"] * rec["height"]
        prev = label_winner.get(cap.label)
        if prev is None:
            label_winner[cap.label] = idx
        else:
            prev_area = pending[prev]["width"] * pending[prev]["height"]
            if area > prev_area:
                label_winner[cap.label] = idx

    out: list[dict] = []
    for idx, rec in enumerate(pending):
        cap = rec["_cap"]
        if cap is not None and label_winner.get(cap.label) != idx:
            cap = None
        # Caption-only policy: drop images without a matched caption.
        # Uncaptioned image binaries are almost always page-graphic
        # noise (decorative elements, equation glyphs as raster images,
        # rules, headers/logos) that pymupdf reports as image objects
        # but have no semantic anchor. Without a caption there's no way
        # for the chunk linker to associate them with body text and the
        # extract handler can't reason about them. The 47 pN_imgM
        # binaries we used to keep added 29% noise to mvp20 with zero
        # downstream value. The previous behavior is restored by
        # passing ``keep_uncaptioned=True`` for diagnostic ingests.
        if cap is None:
            continue
        if cap in avail:
            avail.remove(cap)
        _consume_md_caption(md_captions, cap.label)
        rec["caption"] = cap.text
        rec["label"] = cap.label
        rec["media_type"] = cap.media_type
        rec["alt_text"] = ""
        rec.pop("_cap", None)
        rec.pop("_img_index", None)
        out.append(rec)
    return out


def _scanned_page_raw(
    page,
    page_num: int,
    seen: set[str],
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
) -> list[dict]:
    """Scanned-page fallback: render the whole page as a PNG.

    When a page is scanned (no extractable image objects) but our
    caption matcher found one or more figure captions on it, we render
    the full page raster and use it as the visual backing for the FIRST
    matched caption only. Previously this loop emitted a separate
    binary for every caption on the page — but the binary was always
    the same page rendering, which produced byte-identical duplicates
    on disk (Chua page 11 had Fig. 7 and Fig. 8 both backed by the same
    page-bytes blob). The dedup hash discriminated by ``cap.label`` so
    the in-memory ``seen`` set didn't catch them.

    Now we hash the raw ``page_bytes`` once and emit a single record
    bound to the first caption. Subsequent captions on the same scanned
    page surface as ``Document.figure_refs`` (extracted from the body
    text) — the figure linker will still pick them up via inline
    ``Fig. N`` references.
    """
    figure_captions = [c for c in page_captions if c.media_type != "table"]
    if not figure_captions:
        return []
    try:
        pixmap = page.get_pixmap(dpi=150)
        page_bytes = pixmap.tobytes("png")
    except Exception:
        return []
    h = hashlib.sha256(page_bytes).hexdigest()
    if h in seen:
        return []
    seen.add(h)
    cap = figure_captions[0]
    _consume_md_caption(md_captions, cap.label)
    return [
        {
            "bytes": page_bytes,
            "ext": "png",
            "page": page_num + 1,
            "width": pixmap.width,
            "height": pixmap.height,
            "content_hash": h,
            "bbox": None,
            "caption": cap.text,
            "label": cap.label,
            "media_type": cap.media_type,
            "alt_text": "",
        }
    ]


def _extract_captions_from_page(page) -> list[_CaptionMatch]:
    out: list[_CaptionMatch] = []
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return out
    for block in blocks:
        if len(block) < 5:
            continue
        text = block[4]
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        y = block[1]
        for pattern, media_type in _CAPTION_PATTERNS:
            m = pattern.match(stripped)
            if m:
                label = m.group(1).strip()
                caption = m.group(2).strip()
                out.append(
                    _CaptionMatch(
                        label=label,
                        text=caption if caption else stripped,
                        media_type=media_type,
                        y_position=y,
                    )
                )
                break
    return out


def _extract_captions_from_markdown(md_text: str) -> list[_CaptionMatch]:
    out: list[_CaptionMatch] = []
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = stripped.replace("**", "")
        for pattern, media_type in _CAPTION_PATTERNS:
            m = pattern.match(cleaned)
            if m:
                label = m.group(1).strip()
                caption = m.group(2).strip()
                out.append(
                    _CaptionMatch(
                        label=label,
                        text=caption if caption else cleaned,
                        media_type=media_type,
                    )
                )
                break
    return out


def _match_caption(
    bbox: list[float] | None,
    page_captions: list[_CaptionMatch],
    md_captions: list[_CaptionMatch],
    exclude_type: str | None = None,
) -> _CaptionMatch | None:
    candidates = page_captions
    if exclude_type:
        candidates = [c for c in candidates if c.media_type != exclude_type]
    if not candidates:
        md = md_captions
        if exclude_type:
            md = [c for c in md if c.media_type != exclude_type]
        return md[0] if md else None
    if bbox and len(bbox) >= 4:
        fig_bottom = bbox[3]
        best = None
        best_dist = float("inf")
        for cap in candidates:
            if cap.y_position is not None:
                dist = abs(cap.y_position - fig_bottom)
                if dist < best_dist:
                    best_dist = dist
                    best = cap
        if best:
            return best
    return candidates[0] if candidates else None


def _consume_md_caption(md_captions: list[_CaptionMatch], label: str) -> None:
    for i, mc in enumerate(md_captions):
        if mc.label == label:
            md_captions.pop(i)
            return


def _find_image_bbox(page, xref: int) -> list[float] | None:
    try:
        for img in page.get_image_info(xrefs=True):
            if img.get("xref") == xref:
                bbox = img.get("bbox")
                if bbox:
                    return list(bbox)
    except Exception:
        pass
    return None


def _dict_to_raw_image(d: dict) -> RawImage:
    """Convert an internal image dict to the typed RawImage contract."""
    bbox = d.get("bbox")
    return RawImage(
        data=d.get("bytes"),
        url=d.get("url"),
        ext=(d.get("ext") or "png").lstrip("."),
        caption=d.get("caption", "") or "",
        alt_text=d.get("alt_text", "") or "",
        label=d.get("label"),
        page=d.get("page"),
        media_type=d.get("media_type"),
        bbox=tuple(bbox) if bbox else None,
        width=d.get("width"),
        height=d.get("height"),
        content_hash=d.get("content_hash"),
    )
