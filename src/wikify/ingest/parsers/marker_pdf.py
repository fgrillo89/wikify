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

from ._citations import (
    bracketize_bare_refs,
    bracketize_sup_refs,
    count_ref_list_items_from_md,
    split_adjacent_refs,
)
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

    # Restore bracketed citation markers so the graph can resolve them.
    # Must run BEFORE image-link stripping, otherwise any <sup>N</sup>
    # swallowed by light_clean would be lost.
    md_text = bracketize_sup_refs(md_text)
    md_text = split_adjacent_refs(md_text)
    ref_count = count_ref_list_items_from_md(md_text)
    md_text = bracketize_bare_refs(md_text, ref_count=ref_count)

    # Extract images against the raw rendered markdown so caption
    # matching sees the original ``![...](name)`` placement.
    images = _extract_images(rendered)

    md_text = _light_clean(md_text)
    # Marker emits links to internal block ids (_page_0_Figure_18.jpeg)
    # that do not match the pipeline-saved filenames. Drop the links;
    # images are persisted via RawImage sidecars and DocImage caption
    # chunks, matching Docling's behavior.
    md_text = _strip_image_links(md_text)

    metadata = _extract_metadata(md_text, path)
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


_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# Remaining <sup>...</sup> after bracketize_sup_refs are non-numeric
# affiliation markers (``<sup>b</sup>``) that should never reach author
# metadata or the persisted bib file.
_SUP_TAG_RE = re.compile(r"<sup>[^<]*</sup>", re.IGNORECASE)


def _sanitize_author(name: str) -> str:
    """Drop affiliation sup tags and tidy trailing punctuation from a name."""
    name = _SUP_TAG_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.;")
    return name


def _strip_image_links(md: str) -> str:
    """Remove ``![alt](name)`` markdown image links.

    Marker emits links that point at internal block-id filenames
    (``_page_0_Figure_18.jpeg``) which never match the sanitized names
    written by ``save_doc_images``. Stripping them prevents dead
    references in the persisted corpus markdown; the images are still
    available via sidecars and caption chunks.
    """
    if not md:
        return md
    md = _IMAGE_LINK_RE.sub("", md)
    # Strip leftover blank lines that were only holding an image.
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md


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
    authors = [a for a in (_sanitize_author(a) for a in authors) if a]
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

# Extracts the figure/table number from a caption prefix.
_CAPTION_LABEL_RE = re.compile(
    r"^(?P<kind>Fig(?:ure)?|Table|Scheme)\.?\s*(?P<num>\d+)(?P<sub>[a-z])?",
    re.IGNORECASE,
)


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

        # Parse page from block-id key. Do not derive a label from the
        # block index -- it has no relationship to the caption's real
        # figure number and was producing labels like "Figure 19" for
        # captions reading "Figure 1".
        page = None
        key_match = _IMAGE_KEY_RE.search(name_str)
        if key_match:
            page = int(key_match.group(1))

        caption = caption_map.get(name_str, "")
        label = _label_from_caption(caption)

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        data = buf.getvalue()

        content_hash = hashlib.sha1(data).hexdigest()[:12]
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


def _label_from_caption(caption: str) -> str | None:
    """Return a normalized ``Figure 1`` / ``Table 2a`` label from a caption.

    ``save_doc_images`` parses this label into the final ``Figure_01``
    filename stem. When no recognizable prefix is present we return
    ``None`` and let the image fall back to ``fig_{index:03d}``.
    """
    if not caption:
        return None
    m = _CAPTION_LABEL_RE.match(caption.lstrip())
    if not m:
        return None
    kind_raw = m.group("kind").lower()
    kind = "Figure" if kind_raw.startswith("fig") else kind_raw.capitalize()
    num = int(m.group("num"))
    sub = (m.group("sub") or "").lower()
    return f"{kind} {num}{sub}"
