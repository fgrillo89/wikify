"""PDF parser using Marker (VikParuchuri/marker).

Uses Marker's surya-based pipeline for layout detection, OCR, equation
extraction (LaTeX), and table recognition.  GPU-accelerated via CUDA
automatically when available.

Marker outputs markdown with:
- Equations as ``$...$`` (inline) and ``$$...$$`` (display) LaTeX blocks
- Images extracted as PIL Images (saved to disk by the ingest pipeline)
- Tables as markdown pipe-tables
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from ._sections import section_spans
from .registry import ParseResult, RawImage

_CONVERTER = None


def supported_extensions() -> set[str]:
    return {".pdf"}


def _get_converter():
    """Build and cache the Marker PdfConverter.

    Loads all surya models (layout, OCR, equation, table) once.
    Uses CUDA automatically when available.
    """
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER

    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    artifact_dict = create_model_dict()
    _CONVERTER = PdfConverter(
        artifact_dict=artifact_dict,
        renderer="marker.renderers.markdown.MarkdownRenderer",
    )
    return _CONVERTER


def parse(path: Path, *, hybrid_chunks: bool = True) -> ParseResult:
    converter = _get_converter()
    rendered = converter(str(path.resolve()))

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


# Minimum pixel dimension -- drop logos and decorative elements.
_MIN_IMAGE_DIM = 150


def _extract_images(rendered) -> list[RawImage]:
    """Extract images from Marker's rendered output."""
    images: list[RawImage] = []
    for name, pil_img in (rendered.images or {}).items():
        w, h = pil_img.size
        if w < _MIN_IMAGE_DIM and h < _MIN_IMAGE_DIM:
            continue

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        data = buf.getvalue()

        content_hash = hashlib.sha1(data).hexdigest()[:12]
        images.append(
            RawImage(
                data=data,
                ext="png",
                caption="",  # Marker doesn't provide per-image captions
                content_hash=content_hash,
            )
        )
    return images
