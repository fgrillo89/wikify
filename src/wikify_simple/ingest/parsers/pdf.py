"""PDF parser using pymupdf4llm + fitz.

Ported from ``wikify.ingest.pdf`` with all SQLModel/Paper/vault coupling
stripped. Returns a ``ParseResult``. Image bytes are captured as raw
payloads in ``metadata['_raw_images']`` so the refresh pipeline can
persist them (see ``ingest/images.py::save_doc_images``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..metadata import (
    clean_markdown,
    extract_authors_from_markdown,
    extract_doi,
    extract_summary,
    extract_year_from_pdf_meta,
    first_heading,
    is_garbled_title,
    parse_authors,
    parse_filename,
)
from ._sections import section_spans
from .registry import ParseResult


def parse(path: Path) -> ParseResult:
    import fitz  # pymupdf
    import pymupdf4llm

    md_text = ""
    try:
        md_text = pymupdf4llm.to_markdown(str(path), use_ocr=False)
    except Exception:
        md_text = ""

    doc = fitz.open(str(path))
    try:
        action = _classify_pdf_text(md_text, doc)
        if action == "ocr":
            try:
                md_text = pymupdf4llm.to_markdown(
                    str(path), use_ocr=True, force_ocr=True, ocr_language="eng"
                )
            except Exception:
                md_text = _fitz_fallback_markdown(doc)
        elif action == "fitz_fallback":
            md_text = _fitz_fallback_markdown(doc)

        metadata = _extract_metadata(doc, md_text, path.name)
        images_raw = _extract_images(doc)
    finally:
        doc.close()

    title = metadata.get("title") or path.stem
    metadata["_raw_images"] = images_raw
    return ParseResult(
        markdown=md_text,
        sections=section_spans(md_text),
        images=[],  # populated by refresh.py via save_doc_images
        metadata=metadata,
        title=title,
    )


# --- classification + fallbacks -----------------------------------------


def _classify_pdf_text(md_text: str, doc) -> str:
    if len(md_text) == 0:
        return "ocr"
    placeholder_chars = sum(len(m.group()) for m in re.finditer(r"\*\*==>.*?<==\*\*", md_text))
    placeholder_ratio = placeholder_chars / len(md_text)
    if placeholder_ratio < 0.3:
        return "ok"
    raw_text = ""
    for i in range(min(3, doc.page_count)):
        raw_text += doc[i].get_text()
    alphanumeric = sum(1 for c in raw_text if c.isalnum())
    if alphanumeric < 500:
        return "ocr"
    return "fitz_fallback"


def _fitz_fallback_markdown(doc) -> str:
    pages: list[str] = []
    for i in range(doc.page_count):
        raw = doc[i].get_text()
        raw = re.sub(r"-\s*\n\s*", "", raw)
        raw = re.sub(r"(?<!\n)\n(?!\n)", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        pages.append(raw.strip())
    return "\n\n".join(pages)


# --- metadata -----------------------------------------------------------


def _extract_metadata(doc, md_text: str, filename: str) -> dict:
    meta = doc.metadata or {}
    fn_year, fn_author, fn_title = parse_filename(filename)

    title = (meta.get("title") or "").strip()
    if not title or is_garbled_title(title):
        heading = first_heading(md_text)
        title = heading or fn_title or Path(filename).stem
    title = clean_markdown(title)

    authors: list[str] = []
    raw_author = (meta.get("author") or "").strip()
    if raw_author:
        authors = parse_authors(raw_author)
    if not authors:
        authors = extract_authors_from_markdown(md_text)
    if not authors and fn_author:
        authors = [fn_author]

    year = extract_year_from_pdf_meta(meta) or fn_year
    doi = extract_doi(md_text[:3000])
    summary = extract_summary(md_text)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "summary": summary,
    }


# --- images -------------------------------------------------------------


def _extract_images(doc) -> list[dict]:
    """Return raw image records: {'bytes', 'ext', 'page', 'caption'}."""
    raw: list[dict] = []
    try:
        n_pages = doc.page_count
    except Exception:
        return raw
    for page_idx in range(n_pages):
        try:
            page = doc[page_idx]
            images = page.get_images(full=True)
        except Exception:
            continue
        for info in images:
            xref = info[0]
            try:
                img = doc.extract_image(xref)
            except Exception:
                continue
            raw.append(
                {
                    "bytes": img.get("image"),
                    "ext": img.get("ext", "png"),
                    "page": page_idx + 1,
                    "caption": "",
                }
            )
    return raw
