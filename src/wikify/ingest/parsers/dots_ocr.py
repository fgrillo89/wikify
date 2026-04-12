"""PDF parser using dots.ocr (rednote-hilab/dots.ocr).

dots.ocr is a 1.7B VLM that produces structured layout + text from
document images. It handles multilingual text, tables, formulas, and
can generate SVG from charts/diagrams.

**Requirements:**
  - CUDA GPU with >= 6GB VRAM (24GB recommended)
  - Install from source: ``pip install git+https://github.com/rednote-hilab/dots.ocr.git``
  - Or use the client: ``pip install dots-ocr-client``

The parser converts each PDF page to an image, runs dots.ocr on it,
and assembles the results into markdown.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._sections import section_spans
from .registry import ParseResult

# Lazy-loaded model singleton to avoid reloading per file.
_MODEL = None
_PROCESSOR = None


def supported_extensions() -> set[str]:
    return {".pdf"}


def parse(path: Path) -> ParseResult:
    md_pages = _parse_pdf_pages(path)
    md_text = "\n\n".join(md_pages)
    md_text = _light_clean(md_text)

    metadata = _extract_metadata(md_text, path)
    sections = section_spans(md_text)
    title = metadata.get("title") or path.stem

    return ParseResult(
        markdown=md_text,
        sections=sections,
        raw_images=[],
        metadata=metadata,
        title=title,
    )


def _ensure_model():
    """Load the dots.ocr model once per process."""
    global _MODEL, _PROCESSOR
    if _MODEL is not None:
        return

    try:
        from transformers import AutoModelForCausalLM, AutoProcessor
    except ImportError:
        raise ImportError(
            "dots.ocr requires `transformers`. "
            "Install: pip install transformers torch"
        ) from None

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "dots.ocr requires a CUDA GPU (>= 6GB VRAM). "
            "No CUDA device found on this machine."
        )

    _MODEL = AutoModelForCausalLM.from_pretrained(
        "rednote-hilab/dots.ocr",
        device_map="auto",
        torch_dtype=torch.float16,
        trust_remote_code=True,
    )
    _PROCESSOR = AutoProcessor.from_pretrained(
        "rednote-hilab/dots.ocr",
        trust_remote_code=True,
    )


def _parse_pdf_pages(path: Path) -> list[str]:
    """Convert each PDF page to an image and run dots.ocr on it."""
    _ensure_model()

    # Convert PDF pages to images using pypdfium2 (already a docling dep).
    try:
        import pypdfium2
    except ImportError:
        raise ImportError(
            "dots.ocr PDF parsing requires pypdfium2. "
            "Install: pip install pypdfium2"
        ) from None

    pdf = pypdfium2.PdfDocument(str(path.resolve()))
    pages_md: list[str] = []

    for i in range(len(pdf)):
        page = pdf[i]
        # Render at 200 DPI for good OCR quality without excessive memory.
        bitmap = page.render(scale=200 / 72)
        pil_image = bitmap.to_pil()

        md = _ocr_image(pil_image)
        pages_md.append(md)

    pdf.close()
    return pages_md


def _ocr_image(image) -> str:
    """Run dots.ocr on a single PIL image and return markdown."""
    import torch

    prompt = (
        "<|im_start|>user\n<|image|>"
        "Parse this document page to markdown."
        "<|im_end|>\n<|im_start|>assistant\n"
    )
    inputs = _PROCESSOR(
        text=prompt,
        images=image,
        return_tensors="pt",
    ).to(_MODEL.device)

    with torch.no_grad():
        output_ids = _MODEL.generate(
            **inputs,
            max_new_tokens=4096,
            do_sample=False,
        )

    # Decode only the new tokens (skip the prompt).
    prompt_len = inputs["input_ids"].shape[-1]
    result = _PROCESSOR.decode(
        output_ids[0][prompt_len:],
        skip_special_tokens=True,
    )
    return result.strip()


def _light_clean(md: str) -> str:
    if not md:
        return md
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    return md.strip() + "\n"


def _extract_metadata(md_text: str, path: Path) -> dict:
    from wikify.ingest.metadata import (
        clean_markdown,
        extract_authors_from_markdown,
        extract_document_doi,
        extract_publication_fields,
        extract_summary,
        first_heading,
        parse_filename,
    )

    fn_year, fn_author, fn_title = parse_filename(path.name)

    title = clean_markdown(first_heading(md_text) or fn_title or path.stem)
    authors = extract_authors_from_markdown(md_text, fn_author=fn_author)
    if not authors and fn_author:
        authors = [fn_author]

    year = fn_year
    doi = extract_document_doi(md_text)
    publication = extract_publication_fields(md_text)
    summary = extract_summary(md_text)

    metadata = {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "summary": summary,
    }
    metadata.update(publication)
    return metadata
