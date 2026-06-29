"""PDF parser using pymupdf4llm + fitz.

Returns a ``ParseResult`` with typed ``RawImage`` records in the
``raw_images`` field (see ``ingest/images.py::save_doc_images``).
"""

import re
from pathlib import Path

from ..figures import extract_pdf_media
from ..metadata import assemble_pdf_metadata
from ._clean import clean_markdown_text
from ._sections import section_spans, toc_spans
from .registry import ParseResult

_LITE_BACKEND_MISSING = (
    "The 'lite' PDF backend requires the optional 'lite' extra "
    "(pymupdf / pymupdf4llm, AGPL-3.0), which is not in the default "
    "install. Install it with: uv add 'wikify[lite]' "
    "(or pip install 'wikify[lite]'), or use the default 'docling' parser."
)


def parse(path: Path, *, skip_metadata: bool = False) -> ParseResult:
    """Parse a PDF into markdown + images + sections + metadata.

    When ``skip_metadata=True`` the ``assemble_pdf_metadata`` fusion step
    is skipped and ``ParseResult.metadata`` is returned empty. Used by
    the ingest DAG to decouple content parsing (GPU / CPU-bound, pass 3)
    from metadata fusion (pass 4), which runs after DOI batch resolution
    (pass 2). The default preserves the single-pass behaviour for
    direct ``parse_file`` callers and the ``reassemble_metadata`` script.
    """
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ImportError(_LITE_BACKEND_MISSING) from exc

    # Use the layout engine with running-header + running-footer suppression
    # on. pymupdf4llm >=1.27 routes to ``_layout_to_markdown`` by default and
    # its ``header``/``footer`` flags tell the layout model to classify and
    # drop the repeated running boilerplate (page numbers, journal header,
    # copyright footer) at the page level, which is more reliable than
    # downstream line/paragraph regex scrubbing.
    # ``ignore_code`` also suppresses mono-font runs that are almost always
    # stray equation/symbol noise in papers.
    md_text: str = ""
    try:
        md_text = _to_markdown(path, use_ocr=False)
    except Exception:
        md_text = ""

    doc = fitz.open(str(path))
    try:
        action = _classify_pdf_text(md_text, doc)
        if action == "ocr":
            fallback_md = _fitz_fallback_markdown(doc)
            try:
                ocr_md = _to_markdown(path, use_ocr=True)
            except Exception:
                ocr_md = ""
            md_text = _better_markdown(ocr_md, fallback_md)
        elif action == "fitz_fallback":
            fallback_md = _fitz_fallback_markdown(doc)
            try:
                ocr_md = _to_markdown(path, use_ocr=True)
            except Exception:
                ocr_md = ""
            md_text = _better_markdown(ocr_md, fallback_md)

        md_text = _strip_pdf_artifacts(md_text)
        md_text = clean_markdown_text(md_text)
        if skip_metadata:
            metadata = {}
        else:
            metadata = assemble_pdf_metadata(path, md_text, fitz_doc=doc)
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
    return ParseResult(
        markdown=md_text,
        sections=sections,
        raw_images=images_raw,
        metadata=metadata,
        title=title,
    )


# --- classification + fallbacks -----------------------------------------


def _to_markdown(path: Path, *, use_ocr: bool) -> str:
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise ImportError(_LITE_BACKEND_MISSING) from exc

    kwargs = {}
    if use_ocr:
        kwargs["ocr_language"] = "eng"
    return str(
        pymupdf4llm.to_markdown(
            str(path),
            use_ocr=use_ocr,
            force_ocr=use_ocr,
            header=False,
            footer=False,
            ignore_code=True,
            **kwargs,
        )
    )


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


def _better_markdown(first: str, second: str) -> str:
    """Choose the parse with more usable alphanumeric content."""
    first_alnum = sum(1 for c in first if c.isalnum())
    second_alnum = sum(1 for c in second if c.isalnum())
    return first if first_alnum > second_alnum else second


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


# Metadata assembly lives in ``ingest/metadata.py::assemble_pdf_metadata``
# — one priority-chain decision per field, shared across all PDF parsers.

# Image extraction now lives in ``ingest/figures.py::extract_pdf_media``.


# --- pymupdf artifact scrubbing ------------------------------------------

# [12] or [12-15] inline citation markers. We deliberately do NOT match
# single-letter subfigure refs like [a] / [b].
_CITE_RE = re.compile(r"\[\d+(?:-\d+)?\]")

# [token] bracket-wrapping artifact from pymupdf4llm column
# reconstruction. ASCII alnum, length 2-20. ``[Figure 1]`` style refs
# contain a space and do not match.
_BRACKET_WRAP_RE = re.compile(r"\[([A-Za-z0-9]{2,20})\]")

# Bracket-enclosed punctuation / symbol remnants that survive after the
# main citation markers are stripped: ``[,]``, ``[†]``, ``[⊥]``,
# ``[§]``, ``[*]``, ``[‡]``, etc. Produced by pymupdf4llm when it
# wraps footnote anchors in brackets that partially overlap citation
# markers. Each of these is pure noise in the prose.
_BRACKET_JUNK_RE = re.compile(r"\[[\s,;.*†‡§⊥✉⊗]+\]")

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
    md = _BRACKET_JUNK_RE.sub("", md)
    md = _BRACKET_WRAP_RE.sub(r"\1", md)
    md = _DASH_RE.sub("-", md)
    md = _HSPACE_RE.sub(" ", md)
    return md
