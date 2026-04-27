"""Multi-format parser using IBM Docling.

Docling's ``DocumentConverter`` natively handles PDF, DOCX, PPTX, and
HTML — all through one interface that returns a ``DoclingDocument`` we
can lower to markdown + images + sections. This parser exposes
``.pdf``, ``.docx``, ``.pptx``, ``.html``, ``.htm`` as supported, so it
covers the same set as the per-format ``python-docx`` /
``python-pptx`` / ``trafilatura`` parsers when higher-quality
structured output is needed.

PDF still gets the full enrichment pipeline (RT-DETRv2 layout +
TableFormer + optional formulas/VLM). DOCX / PPTX / HTML use Docling's
default pipelines for those formats; Docling already knows how to walk
the native structure, so no custom options are wired.

When ``hybrid_chunks=True`` (the default), the ``ParseResult.metadata``
carries ``_docling_chunks``: a list of ``(text, heading_path)`` pairs
that the pipeline can consume directly, skipping ``chunk_document``.

GPU acceleration is used automatically when CUDA is available.

Enrichment options (PDF only) are controlled via env vars:

  DOCLING_FORMULAS=1       Enable formula/equation extraction (LaTeX)
  DOCLING_PIC_CLASSIFY=1   Enable picture classification
  DOCLING_PIC_DESCRIBE=1   Enable picture description (VLM captioning)
  DOCLING_VLM=1            Use VLM pipeline instead of standard pipeline
  DOCLING_VLM_MODEL=granite  VLM model: granite, smoldocling, got2, glmocr,
                             granite-ollama, granite-vllm
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ._citations import bracketize_bare_refs
from ._sections import section_spans
from .registry import ParseResult, RawImage

_HF_PATCHED = False


@dataclass
class DoclingOptions:
    """Configurable options for the Docling parser.

    All options are controllable via ``DOCLING_*`` environment variables.

    Key options and their performance impact:

    +-----------------+----------+--------------------------------------+
    | Option          | Default  | Impact                               |
    +-----------------+----------+--------------------------------------+
    | formulas        | ON       | +10-20s/paper (granite-docling-258M) |
    | formula_model   | granite  | granite=258M (fast), v2=larger (slow)|
    | ocr             | off      | +5-150s/paper (depends on page count)|
    | images_scale    | 3.0      | native-like resolution (~216 DPI)     |
    | pic_classify    | off      | minor overhead                       |
    | pic_describe    | off      | +5-10s/paper (SmolVLM captioning)    |
    +-----------------+----------+--------------------------------------+

    Formula enrichment uses granite-docling-258M by default. Produces
    proper LaTeX in ``$$...$$`` blocks. Set ``DOCLING_FORMULAS=0`` to
    disable for faster iteration. Formula-heavy papers (>20 regions)
    may take 100s+ even with the granite model.
    """

    hybrid_chunks: bool = True
    formulas: bool = True
    formula_model: str = "granite_docling"  # "granite_docling" or "codeformulav2"
    ocr: bool = False
    pic_classify: bool = False
    pic_describe: bool = False
    vlm: bool = False
    images_scale: float = 3.0
    # Batch sizes for GPU inference (ignored on CPU).
    layout_batch_size: int = 64
    ocr_batch_size: int = 64

    @classmethod
    def from_env(cls) -> DoclingOptions:
        """Build options from DOCLING_* environment variables."""
        return cls(
            formulas=os.environ.get("DOCLING_FORMULAS", "1") != "0",
            formula_model=os.environ.get("DOCLING_FORMULA_MODEL", "granite_docling"),
            ocr=os.environ.get("DOCLING_OCR", "") == "1",
            pic_classify=os.environ.get("DOCLING_PIC_CLASSIFY", "") == "1",
            pic_describe=os.environ.get("DOCLING_PIC_DESCRIBE", "") == "1",
            vlm=os.environ.get("DOCLING_VLM", "") == "1",
            images_scale=float(os.environ.get("DOCLING_IMAGES_SCALE", "3.0")),
        )



def _patch_hf_symlinks() -> None:
    """On Windows without Developer Mode, HF hub symlink creation fails.

    Monkey-patch ``_create_symlink`` to fall back to file copy so model
    downloads work without admin privileges.  Applied once per process.
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


_DYNAMO_PATCHED = False


def _disable_torch_compile_on_windows() -> None:
    """Disable torch.compile on Windows where triton is unavailable.

    Triton is OpenAI's GPU compiler for fused kernels -- it only supports
    Linux.  Docling's CodeFormula enrichment model triggers torch.compile
    which fails on Windows without triton.  Setting ``suppress_errors``
    makes torch fall back to eager execution (same results, slightly
    slower on large batches, no crash).
    """
    global _DYNAMO_PATCHED
    if _DYNAMO_PATCHED or sys.platform != "win32":
        return
    _DYNAMO_PATCHED = True
    try:
        import torch._dynamo

        torch._dynamo.config.suppress_errors = True
    except ImportError:
        pass


_CACHED_CONVERTER = None
_CACHED_OPTS_KEY = None


def _get_converter(opts: DoclingOptions):
    """Return a cached converter, rebuilding only if options changed."""
    global _CACHED_CONVERTER, _CACHED_OPTS_KEY
    key = (opts.formulas, opts.formula_model, opts.ocr, opts.pic_classify,
           opts.pic_describe, opts.vlm, opts.images_scale,
           opts.layout_batch_size, opts.ocr_batch_size)
    if _CACHED_CONVERTER is None or _CACHED_OPTS_KEY != key:
        _CACHED_CONVERTER = _build_converter(opts)
        _CACHED_OPTS_KEY = key
    return _CACHED_CONVERTER


def parse(
    path: Path,
    *,
    hybrid_chunks: bool = True,
    skip_metadata: bool = False,
) -> ParseResult:
    """Parse a PDF via Docling into markdown + images + sections + metadata.

    When ``skip_metadata=True`` the ``assemble_pdf_metadata`` fusion is
    skipped (ingest DAG pass 3 -> pass 4 decoupling). The
    ``_docling_chunks`` HybridChunker payload still rides in
    ``metadata`` because downstream chunking depends on it; the fusion
    step in pass 4 merges its output over that payload.
    """
    _patch_hf_symlinks()
    _disable_torch_compile_on_windows()

    opts = DoclingOptions.from_env()
    opts.hybrid_chunks = hybrid_chunks

    effective_opts = opts
    converter = _get_converter(opts)

    try:
        result = converter.convert(str(path.resolve()))
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower() or "CUDA" in str(exc):
            # VRAM exhausted on large PDF -- retry without GPU-heavy
            # enrichments (formulas, pic_describe) to fit in memory.
            import copy

            effective_opts = copy.copy(opts)
            effective_opts.formulas = False
            effective_opts.pic_describe = False
            sys.stderr.write(
                f"[docling] CUDA OOM on {path.name}, "
                f"retrying without formula enrichment\n"
            )
            converter = _get_converter(effective_opts)
            result = converter.convert(str(path.resolve()))
        else:
            raise
    doc = result.document

    md_text = doc.export_to_markdown()
    md_text = _light_clean(md_text, formulas_enabled=effective_opts.formulas)

    # Count bibliography entries for bracketize_refs range validation.
    ref_count = _count_ref_list_items(doc)
    md_text = bracketize_bare_refs(md_text, ref_count=ref_count)

    if skip_metadata:
        metadata: dict = {}
    else:
        from wikify.ingest.metadata import assemble_pdf_metadata, parse_filename

        # Docling's DoclingDocument carries its own ``doc.name`` — often a
        # filename-derived placeholder but occasionally a useful title. Pass
        # it to the shared priority chain as an extra candidate; junk/length
        # filters will drop it when worthless.
        extra = ""
        if hasattr(doc, "name") and doc.name:
            extra = str(doc.name).strip()
        metadata = assemble_pdf_metadata(path, md_text, extra_title_candidate=extra)
        # Parser-specific post-hoc guard: Docling occasionally produces an
        # all-caps title that ``is_junk_title`` does not flag. Fall back to
        # ``fn_title`` when that happens.
        if _is_likely_noise_title(metadata.get("title", "")):
            _, _, fn_title = parse_filename(path.name)
            if fn_title:
                metadata["title"] = fn_title
    images = _extract_images(doc)
    sections = section_spans(md_text)

    title = metadata.get("title") or path.stem

    if opts.hybrid_chunks:
        chunks_data = _hybrid_chunk(doc)
        metadata["_docling_chunks"] = chunks_data

    return ParseResult(
        markdown=md_text,
        sections=sections,
        raw_images=images,
        metadata=metadata,
        title=title,
    )


# ---------------------------------------------------------------------------
# Converter construction
# ---------------------------------------------------------------------------


def _has_cuda() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def _make_accelerator():
    """Build AcceleratorOptions, preferring CUDA when available."""
    from docling.datamodel.accelerator_options import AcceleratorOptions

    device = "cuda" if _has_cuda() else "cpu"
    return AcceleratorOptions(device=device)


def _build_converter(opts: DoclingOptions):
    """Build a DocumentConverter from options, choosing the right pipeline.

    PDF gets the full enrichment pipeline (layout + tables + optional
    formulas/VLM). DOCX, PPTX, and HTML are declared in
    ``allowed_formats`` so Docling will dispatch to its native parsers
    for those formats without any custom options.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if opts.vlm:
        return _build_vlm_converter()

    accel = _make_accelerator()
    pipeline_opts = _make_standard_options(accel, opts)
    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.DOCX,
            InputFormat.PPTX,
            InputFormat.HTML,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_opts,
            ),
        },
    )


def _make_standard_options(accel, opts: DoclingOptions):
    """Standard pipeline options with enrichments."""
    if _has_cuda():
        from docling.datamodel.pipeline_options import (
            ThreadedPdfPipelineOptions as PipelineCls,
        )
    else:
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions as PipelineCls,
        )

    kwargs: dict = {
        "accelerator_options": accel,
        "generate_picture_images": True,
        "images_scale": opts.images_scale,
        "do_ocr": opts.ocr,
        "do_formula_enrichment": opts.formulas,
        "do_picture_classification": opts.pic_classify,
        "do_picture_description": opts.pic_describe,
    }

    if opts.formulas:
        try:
            from docling.datamodel.pipeline_options import (
                CodeFormulaVlmOptions,
            )
        except ImportError:
            pass  # docling version without formula support
        else:
            kwargs["code_formula_options"] = (
                CodeFormulaVlmOptions.from_preset(opts.formula_model)
            )

    if _has_cuda():
        kwargs["layout_batch_size"] = opts.layout_batch_size
        kwargs["ocr_batch_size"] = opts.ocr_batch_size

    return PipelineCls(**kwargs)


# VLM model lookup table. Keyed by DOCLING_VLM_MODEL env var value.
# Entries reference constants in docling.datamodel.vlm_model_specs.
_VLM_MODELS: dict[str, str] = {
    "granite": "GRANITEDOCLING_TRANSFORMERS",
    "smoldocling": "SMOLDOCLING_TRANSFORMERS",
    "got2": "GOT2_TRANSFORMERS",
    "glmocr": "GLMOCR_TRANSFORMERS",
    "granite-ollama": "GRANITEDOCLING_OLLAMA",
    "granite-vllm": "GRANITEDOCLING_VLLM_API",
}


def _build_vlm_converter():
    """Build a VLM pipeline converter.

    Model selection via ``DOCLING_VLM_MODEL`` env var (default: granite).
    Supported values: granite, smoldocling, got2, glmocr, granite-ollama,
    granite-vllm.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.vlm_pipeline import VlmPipeline

    model_key = os.environ.get("DOCLING_VLM_MODEL", "granite")
    spec_name = _VLM_MODELS.get(model_key)
    if spec_name is None:
        raise ValueError(
            f"unknown DOCLING_VLM_MODEL={model_key!r}; "
            f"available: {sorted(_VLM_MODELS)}"
        )

    from docling.datamodel import vlm_model_specs

    vlm_opts = getattr(vlm_model_specs, spec_name)
    pipeline_options = VlmPipelineOptions(vlm_options=vlm_opts)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=pipeline_options,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def _count_ref_list_items(doc) -> int:
    """Count bibliography entries in the DoclingDocument.

    Only counts ListItems that appear after the last section header
    containing 'reference' or 'bibliography'. This avoids counting
    bullet lists in the body as bibliography entries.
    """
    try:
        from docling.datamodel.document import ListItem, SectionHeaderItem

        items = list(doc.iterate_items())
        # Find the last references/bibliography header
        last_ref_idx = -1
        for i, (item, _) in enumerate(items):
            if isinstance(item, SectionHeaderItem):
                text = getattr(item, "text", "").lower()
                if "reference" in text or "bibliography" in text:
                    last_ref_idx = i
        if last_ref_idx < 0:
            return 0
        # Count ListItems after that header
        return sum(
            1 for item, _ in items[last_ref_idx:]
            if isinstance(item, ListItem)
        )
    except Exception:
        return 0


def _is_likely_noise_title(title: str) -> bool:
    """True if title looks like a section header, not a paper title."""
    from wikify.ingest.metadata import _is_heading_noise

    if _is_heading_noise(title):
        return True
    # All-caps titles are usually section headers or OCR artifacts
    if title.isupper():
        return True
    return False


def _light_clean(md: str, *, formulas_enabled: bool = False) -> str:
    """Minimal cleanup -- Docling output is already cleaner than pymupdf."""
    if not md:
        return md
    # Strip image placeholders (images are extracted separately).
    md = re.sub(r"<!--\s*image\s*-->", "", md)
    # Only strip formula placeholders if formula enrichment is OFF.
    if not formulas_enabled:
        md = re.sub(r"<!--\s*formula-not-decoded\s*-->", "", md)
    # Collapse 3+ blank lines.
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Strip trailing whitespace per line.
    md = re.sub(r"[ \t]+\n", "\n", md)
    return md.strip() + "\n"


# Metadata assembly lives in ``ingest/metadata.py::assemble_pdf_metadata``.
# Docling-specific quirks handled in ``parse()``: ``doc.name`` feeds the
# shared chain as ``extra_title_candidate``, and a post-hoc
# ``_is_likely_noise_title`` guard catches all-caps / section-header titles
# Docling occasionally produces that ``is_junk_title`` misses.


# Minimum pixel dimension for a real figure. Images smaller than this
# in both width and height are logos, decorative elements, or equation
# glyphs and are dropped at extraction time (zero-cost filter).
_MIN_IMAGE_DIM = 150


def _extract_images(doc) -> list[RawImage]:
    """Extract images from DoclingDocument.

    Requires ``generate_picture_images=True`` in pipeline options so
    Docling renders each PictureItem's bounding-box crop into an
    ``ImageRef`` with a PIL image or URI.

    Images smaller than ``_MIN_IMAGE_DIM`` in both dimensions are
    dropped as logos or decorative elements.
    """
    import io as _io

    images: list[RawImage] = []
    try:
        from docling.datamodel.document import PictureItem

        for item, _level in doc.iterate_items():
            if not isinstance(item, PictureItem):
                continue

            caption = ""
            if hasattr(item, "caption_text"):
                caption = item.caption_text(doc) or ""

            page = None
            if hasattr(item, "prov") and item.prov:
                page = item.prov[0].page_no

            data = _image_bytes_from_item(item)
            if data is None:
                continue

            # Drop tiny images (logos, decorative elements).
            try:
                from PIL import Image as PilImage  # noqa: N813

                pil = PilImage.open(_io.BytesIO(data))
                w, h = pil.size
                if w < _MIN_IMAGE_DIM and h < _MIN_IMAGE_DIM:
                    continue
            except Exception:
                pass

            content_hash = hashlib.sha1(data).hexdigest()[:12]
            images.append(
                RawImage(
                    data=data,
                    ext="png",
                    caption=caption,
                    page=page,
                    content_hash=content_hash,
                )
            )
    except Exception:
        pass
    return images


def _image_bytes_from_item(item) -> bytes | None:
    """Get PNG bytes from a Docling PictureItem."""
    import io

    img_ref = getattr(item, "image", None)
    if img_ref is None:
        return None

    pil_img = getattr(img_ref, "pil_image", None)
    if pil_img is not None:
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return buf.getvalue()

    uri_str = str(img_ref.uri) if getattr(img_ref, "uri", None) else ""
    if not uri_str:
        return None

    if uri_str.startswith("data:"):
        import base64

        try:
            _, b64 = uri_str.split(",", 1)
            return base64.b64decode(b64)
        except Exception:
            return None

    p = Path(uri_str)
    if p.exists():
        try:
            return p.read_bytes()
        except Exception:
            return None

    return None


def _hybrid_chunk(doc) -> list[dict]:
    """Use Docling's HybridChunker to produce structure-aware chunks."""
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

    chunker = HybridChunker(max_tokens=2000, merge_peers=True)
    chunks: list[dict] = []
    for chunk in chunker.chunk(doc):
        text = chunker.contextualize(chunk)
        heading_path = ["body"]
        if hasattr(chunk, "meta") and chunk.meta:
            export = getattr(chunk.meta, "export_json_dict", None)
            meta_dict = export() if export else {}
            headings = meta_dict.get("headings", [])
            if headings:
                heading_path = headings
        chunks.append({
            "text": text,
            "heading_path": heading_path,
        })
    return chunks
