"""PDF parser using pymupdf4llm + fitz.

Returns a ``ParseResult``. Image bytes are captured as raw
payloads in ``metadata['_raw_images']`` so the refresh pipeline can
persist them (see ``ingest/images.py::save_doc_images``).
"""

import re
from pathlib import Path

from ..figures import extract_pdf_media
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
from ._clean import clean_markdown_text
from ._sections import section_spans, toc_spans
from .registry import ParseResult


def parse(path: Path) -> ParseResult:
    import fitz  # pymupdf
    import pymupdf4llm

    # Use the layout engine with running-header + running-footer suppression
    # on. pymupdf4llm >=1.27 routes to ``_layout_to_markdown`` by default and
    # its ``header``/``footer`` flags tell the layout model to classify and
    # drop the repeated running boilerplate (page numbers, journal header,
    # copyright footer) at the page level — much more reliable than the
    # downstream line/paragraph regex scrubbing we previously leaned on.
    # ``ignore_code`` also suppresses mono-font runs that are almost always
    # stray equation/symbol noise in papers.
    md_text: str = ""
    try:
        md_text = str(
            pymupdf4llm.to_markdown(
                str(path),
                use_ocr=False,
                header=False,
                footer=False,
                ignore_code=True,
            )
        )
    except Exception:
        md_text = ""

    doc = fitz.open(str(path))
    try:
        action = _classify_pdf_text(md_text, doc)
        if action == "ocr":
            try:
                md_text = str(
                    pymupdf4llm.to_markdown(
                        str(path),
                        use_ocr=True,
                        force_ocr=True,
                        ocr_language="eng",
                        header=False,
                        footer=False,
                        ignore_code=True,
                    )
                )
            except Exception:
                md_text = _fitz_fallback_markdown(doc)
        elif action == "fitz_fallback":
            md_text = _fitz_fallback_markdown(doc)

        md_text = _strip_pdf_artifacts(md_text)
        md_text = clean_markdown_text(md_text)
        metadata = _extract_metadata(doc, md_text, path.name)
        images_raw = extract_pdf_media(doc, md_text)
        # Capture the PDF bookmark TOC. When the document ships a real
        # outline (>= 3 entries) we use it as the canonical section
        # source — its titles are typically more reliable than what
        # pymupdf4llm derives from heading detection alone, especially
        # for technical reports with deep numbered hierarchies.
        try:
            toc_entries = doc.get_toc() or []
        except Exception:
            toc_entries = []
    finally:
        doc.close()

    sections = None
    if toc_entries and len(toc_entries) >= 3:
        sections = toc_spans(md_text, toc_entries)
    if sections is None:
        sections = section_spans(md_text)

    title = metadata.get("title") or path.stem
    metadata["_raw_images"] = images_raw
    return ParseResult(
        markdown=md_text,
        sections=sections,
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
    """Reconstruct page markdown from fitz block-level extraction.

    ``get_text("blocks")`` yields one block per visually separated text
    unit (paragraph-like), so we can rebuild a markdown-ish flow with
    real blank-line paragraph breaks instead of the prior one-line-per-
    page join that destroyed downstream cleanup heuristics. Hyphenation
    across line breaks within a block is rejoined; remaining newlines
    become spaces. Non-text blocks (images) are skipped.
    """
    pages: list[str] = []
    for i in range(doc.page_count):
        blocks = doc[i].get_text("blocks")
        paras: list[str] = []
        for b in blocks:
            # block tuple: (x0, y0, x1, y1, text, block_no, block_type)
            if len(b) < 7 or b[6] != 0:  # 0 = text block
                continue
            text = (b[4] or "").strip()
            if not text:
                continue
            text = re.sub(r"-\s*\n\s*", "", text)  # rejoin hyphenated line breaks
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)  # fragmented lines
            text = re.sub(r"\s{2,}", " ", text).strip()
            if text:
                paras.append(text)
        if paras:
            pages.append("\n\n".join(paras))
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

    # Authors: in-document extraction (markdown body) wins over PDF metadata
    # when it yields a richer list. PDF metadata often carries only the
    # corresponding author (e.g. "H. Kim") even when the paper has 12 real
    # authors. Pass the filename-derived surname as an anchor so the
    # extractor can skip over landing-page recommendation blocks whose
    # author lists come from unrelated papers. Filename parse is the
    # last-resort fallback.
    md_authors = extract_authors_from_markdown(md_text, fn_author=fn_author)
    raw_author = (meta.get("author") or "").strip()
    meta_authors = parse_authors(raw_author) if raw_author else []
    if len(md_authors) >= 2:
        authors = md_authors
    elif meta_authors:
        authors = meta_authors
    elif md_authors:
        authors = md_authors
    elif fn_author:
        authors = [fn_author]
    else:
        authors = []

    # Year: filename year wins over PDF creation/mod date because the
    # latter reflects when the file was last touched, not the publication
    # year (Chua 1971 → 1999, Matveyev 2015 → 2026 etc). On a miss, year
    # is None — never the current year.
    year = fn_year or extract_year_from_pdf_meta(meta)
    doi = extract_doi(md_text[:3000])
    summary = extract_summary(md_text)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "summary": summary,
    }


# Image extraction now lives in ``ingest/images.py::extract_pdf_media``.


# --- pymupdf artifact scrubbing ------------------------------------------

# [12] or [12-15] inline citation markers. We deliberately do NOT match
# single-letter subfigure refs like [a] / [b].
_CITE_RE = re.compile(r"\[\d+(?:-\d+)?\]")

# [token] bracket-wrapping artifact from pymupdf4llm column
# reconstruction. ASCII alnum, length 2-20. ``[Figure 1]`` style refs
# contain a space and do not match.
_BRACKET_WRAP_RE = re.compile(r"\[([A-Za-z0-9]{2,20})\]")

# Unicode dash variants -> ASCII '-'
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_DASH_RE = re.compile(f"[{re.escape(_DASHES)}]")

# Runs of spaces/tabs (NOT newlines) collapse to a single space.
_HSPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_pdf_artifacts(md: str) -> str:
    """Scrub common pymupdf4llm / column-reconstruction artifacts.

    Applied once after the parser emits markdown and before TOC merge,
    section detection, image extraction, or chunking. This cleans the
    text for the embedder, the model, the validator, and search at the
    same time.
    """
    if not md:
        return md
    md = _CITE_RE.sub("", md)
    md = _BRACKET_WRAP_RE.sub(r"\1", md)
    md = _DASH_RE.sub("-", md)
    md = _HSPACE_RE.sub(" ", md)
    return md
