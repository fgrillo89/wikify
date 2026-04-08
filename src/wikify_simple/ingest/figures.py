"""PDF figure/media extraction ported from ``wikify.ingest.extract.media``.

The legacy caption-matching, dedup, scan detection, and bbox logic are
carried over verbatim where possible. The only structural change is the
return shape: instead of SQLModel ``Figure`` instances this module
returns plain dicts that ``ingest/images.py::save_doc_images`` writes to
disk alongside a JSON sidecar.
"""

from __future__ import annotations

import hashlib
import re

_MAX_MEDIA_PER_PAPER = 80
_MIN_WIDTH = 100
_MIN_HEIGHT = 100
_MIN_BYTES = 2000
_SCAN_THRESHOLD = 15

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


def extract_pdf_media(doc, md_text: str) -> list[dict]:
    """Return a list of raw image dicts extracted from an open fitz Document.

    Each dict has ``{bytes, ext, page, caption, label, media_type, bbox,
    width, height, content_hash}``. Dedup is by content sha256.
    """
    raw: list[dict] = []
    seen: set[str] = set()
    md_captions = _extract_captions_from_markdown(md_text)

    try:
        n_pages = doc.page_count
    except Exception:
        return raw

    for page_num in range(n_pages):
        if len(raw) >= _MAX_MEDIA_PER_PAPER:
            break
        try:
            page = doc[page_num]
            image_list = page.get_images(full=True)
        except Exception:
            continue

        page_captions = _extract_captions_from_page(page)

        if len(image_list) > _SCAN_THRESHOLD:
            raw.extend(_scanned_page_raw(page, page_num, seen, page_captions, md_captions))
            continue

        extracted = _extract_images_on_page(doc, page, image_list, seen)
        raw.extend(_build_records(page_num, extracted, page_captions, md_captions))

    return raw[:_MAX_MEDIA_PER_PAPER]


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
        if width < _MIN_WIDTH or height < _MIN_HEIGHT:
            continue
        if len(blob) < _MIN_BYTES:
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
        if cap is not None:
            if cap in avail:
                avail.remove(cap)
            _consume_md_caption(md_captions, cap.label)
        rec["caption"] = cap.text if cap else ""
        rec["label"] = cap.label if cap else f"p{page_num + 1}_img{rec['_img_index']}"
        rec["media_type"] = cap.media_type if cap else "figure"
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
    figure_captions = [c for c in page_captions if c.media_type != "table"]
    if not figure_captions:
        return []
    try:
        pixmap = page.get_pixmap(dpi=150)
        page_bytes = pixmap.tobytes("png")
    except Exception:
        return []
    out: list[dict] = []
    for cap in figure_captions:
        h = hashlib.sha256((str(page_num) + cap.label).encode("utf-8") + page_bytes).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append(
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
        )
        _consume_md_caption(md_captions, cap.label)
    return out


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
