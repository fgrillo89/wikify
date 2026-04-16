"""PDF parser using Marker (VikParuchuri/marker).

Uses Marker's surya-based pipeline for layout detection, OCR, equation
extraction (LaTeX), and table recognition.  GPU-accelerated via CUDA
automatically when available.

Marker outputs markdown with:
- Equations as ``$...$`` (inline) and ``$$...$$`` (display) LaTeX blocks
- Citations as ``<sup>N</sup>`` superscripts (no bracket stripping)
- Images extracted as PIL Images (saved to disk by the ingest pipeline)
- Tables as markdown pipe-tables

Configuration:
- Batch sizes auto-tune per device (12/4 detection, 64/32 OCR on CUDA/CPU)
- dtype: bfloat16 on CUDA, float32 otherwise (no quantization available)
- Models auto-downloaded from HuggingFace (surya-based)
- LLM processors are included but no-op without a configured API key
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
from pathlib import Path

from ._sections import section_spans
from .registry import ParseResult, RawImage

_CONVERTER = None


def supported_extensions() -> set[str]:
    return {".pdf"}


# ---------------------------------------------------------------------------
# Windows patches (shared concern with docling)
# ---------------------------------------------------------------------------

_HF_PATCHED = False
_DYNAMO_PATCHED = False


def _patch_hf_symlinks() -> None:
    """On Windows without Developer Mode, HF hub symlink creation fails.

    Monkey-patch ``_create_symlink`` to fall back to file copy so surya
    model downloads work without admin privileges.
    """
    global _HF_PATCHED
    if _HF_PATCHED or sys.platform != "win32":
        return
    _HF_PATCHED = True
    import huggingface_hub.file_download as fd

    _original = fd._create_symlink

    def _safe(src, dst, new_blob=False):
        try:
            _original(src, dst, new_blob=new_blob)
        except OSError:
            import shutil

            dst_str = str(dst)
            if os.path.exists(dst_str):
                os.remove(dst_str)
            os.makedirs(os.path.dirname(dst_str), exist_ok=True)
            shutil.copy2(str(src), dst_str)

    fd._create_symlink = _safe


def _disable_torch_compile_on_windows() -> None:
    """Disable torch.compile on Windows where triton is unavailable."""
    global _DYNAMO_PATCHED
    if _DYNAMO_PATCHED or sys.platform != "win32":
        return
    _DYNAMO_PATCHED = True
    try:
        import torch._dynamo

        torch._dynamo.config.suppress_errors = True
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Converter lifecycle
# ---------------------------------------------------------------------------


def _get_converter():
    """Build and cache the Marker PdfConverter.

    Loads all surya models (layout, OCR, equation, table) once.
    Device and dtype auto-detected by marker's settings module.
    """
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER

    _patch_hf_symlinks()
    _disable_torch_compile_on_windows()

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    artifact_dict = create_model_dict()
    _CONVERTER = PdfConverter(
        artifact_dict=artifact_dict,
        renderer="marker.renderers.markdown.MarkdownRenderer",
        config={"disable_tqdm": True},
    )
    return _CONVERTER


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse(path: Path) -> ParseResult:
    converter = _get_converter()

    try:
        rendered = converter(str(path.resolve()))
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower() or "CUDA" in str(exc):
            sys.stderr.write(
                f"[marker] CUDA OOM on {path.name}; "
                f"marker has no fallback -- re-raising\n"
            )
        raise

    md_text = rendered.markdown
    md_text = _light_clean(md_text)

    metadata = _extract_metadata(md_text, path)
    images = _extract_images(rendered)
    sections = section_spans(md_text)
    title = metadata.get("title") or path.stem

    return ParseResult(
        markdown=md_text,
        sections=sections,
        raw_images=images,
        metadata=metadata,
        title=title,
    )


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def _light_clean(md: str) -> str:
    """Minimal cleanup on Marker output."""
    if not md:
        return md
    # Collapse 3+ blank lines
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace per line
    md = re.sub(r"[ \t]+\n", "\n", md)
    return md.strip() + "\n"


def _extract_metadata(md_text: str, path: Path) -> dict:
    """Extract title, authors, year from markdown + filename."""
    from wikify.ingest.metadata import (
        clean_markdown,
        extract_authors_from_markdown,
        extract_document_doi,
        extract_publication_fields,
        extract_summary,
        first_heading,
        is_garbled_title,
        parse_filename,
    )

    fn_year, fn_author, fn_title = parse_filename(path.name)

    title = first_heading(md_text) or ""
    if not title or is_garbled_title(title):
        title = fn_title or path.stem
    title = clean_markdown(title)

    # Prefer filename title when extraction picks up a section header
    from wikify.ingest.metadata import _is_heading_noise

    if fn_title and (_is_heading_noise(title) or (title.isupper() and fn_title)):
        title = fn_title

    authors = extract_authors_from_markdown(md_text, fn_author=fn_author)
    if not authors and fn_author:
        authors = [fn_author]

    doi = extract_document_doi(md_text)
    publication = extract_publication_fields(md_text)
    summary = extract_summary(md_text)

    metadata = {
        "title": title,
        "authors": authors,
        "year": fn_year,
        "doi": doi,
        "summary": summary,
    }
    metadata.update(publication)
    return metadata


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

# Minimum pixel dimension -- drop logos and decorative elements.
_MIN_IMAGE_DIM = 150

# Image key format from marker's BlockId.to_path():
#   "_page_{page_id}_{BlockType}_{block_id}.{format}"
_IMAGE_KEY_RE = re.compile(
    r"_page_(\d+)_([A-Za-z]+)_(\d+)\.\w+$"
)

# Map marker block type names to human-readable figure labels.
_BLOCK_TYPE_LABELS = {
    "Picture": "Figure",
    "Figure": "Figure",
}


def _extract_images(rendered) -> list[RawImage]:
    """Extract images from Marker's rendered output.

    Parses page number and label from image dict keys (BlockId paths).
    Matches captions from markdown ``![...](image_name)`` references
    near ``Fig(ure)? N`` or ``Table N`` text.
    """
    # Build caption map from markdown: image_name -> caption
    caption_map: dict[str, str] = {}
    if rendered.markdown:
        for m in re.finditer(
            r"!\[([^\]]*)\]\(([^)]+)\)", rendered.markdown,
        ):
            img_name = m.group(2).rsplit("/", 1)[-1]
            after = rendered.markdown[m.end():m.end() + 500]
            cap_match = re.search(
                r"((?:Fig(?:ure)?|Table|Scheme)\.?\s*\d+[^.\n]*\.)",
                after, re.IGNORECASE,
            )
            if cap_match:
                caption_map[img_name] = cap_match.group(1).strip()

    images: list[RawImage] = []
    for name, pil_img in (rendered.images or {}).items():
        # name may be a BlockId object or a string path
        name_str = str(name)

        w, h = pil_img.size
        if w < _MIN_IMAGE_DIM and h < _MIN_IMAGE_DIM:
            continue

        # Parse page and label from key
        page = None
        label = None
        key_match = _IMAGE_KEY_RE.search(name_str)
        if key_match:
            page = int(key_match.group(1))
            block_type = key_match.group(2)
            block_idx = int(key_match.group(3))
            readable = _BLOCK_TYPE_LABELS.get(block_type, block_type)
            label = f"{readable} {block_idx + 1}"

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        data = buf.getvalue()

        content_hash = hashlib.sha1(data).hexdigest()[:12]
        caption = caption_map.get(name_str, "")
        images.append(
            RawImage(
                data=data,
                ext="png",
                caption=caption,
                label=label,
                page=page,
                content_hash=content_hash,
                width=w,
                height=h,
            )
        )
    return images
