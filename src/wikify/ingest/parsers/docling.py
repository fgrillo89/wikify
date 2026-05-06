"""Multi-format parser using IBM Docling.

Docling's ``DocumentConverter`` natively handles PDF, DOCX, PPTX, and
HTML through one interface that returns a ``DoclingDocument``. This
module lowers each input to ``ParseResult`` (markdown + images +
sections + metadata) so the rest of the ingest pipeline does not need
to know which backend produced it.

PDFs go through the full enrichment pipeline (RT-DETRv2 layout +
TableFormer + optional formula/VLM heads). DOCX / PPTX / HTML use
Docling's native parsers for those formats; we only need to declare
them in ``allowed_formats``.

GPU acceleration is automatic when CUDA is available. The standard
PDF pipeline batches layout / OCR / table inference on the GPU; tune
via ``DOCLING_*_BATCH_SIZE`` env vars below.

Enrichment + performance knobs (env vars):

  DOCLING_FORMULAS=1            Formula/equation enrichment (LaTeX)
  DOCLING_FORMULA_MODEL=...     granite_docling (default) | codeformulav2
  DOCLING_ALLOW_CPU_FORMULAS=0  Allow formula enrichment on CPU (very slow)
  DOCLING_OCR=1                 OCR scanned pages (slow on long PDFs)
  DOCLING_PIC_CLASSIFY=1        Picture classification
  DOCLING_PIC_DESCRIBE=1        Picture description (VLM captioning)
  DOCLING_VLM=1                 Use the VLM pipeline (whole-page VLM)
  DOCLING_VLM_MODEL=granite     granite | smoldocling | got2 | glmocr |
                                granite-ollama | granite-vllm
  DOCLING_IMAGES_SCALE=3.0      Picture render resolution multiplier
  DOCLING_LAYOUT_BATCH_SIZE=auto  Layout-detection batch size on GPU
                                  (auto = 8/16/32/64 by VRAM tier)
  DOCLING_OCR_BATCH_SIZE=auto     OCR batch size on GPU (only matters
                                  when DOCLING_OCR=1 or auto-detect
                                  kicks in; auto = 8/16/32/64 by VRAM)
  DOCLING_TABLE_BATCH_SIZE=4    TableFormer batch size (still CPU-bound
                                in current Docling; do not raise blindly)
  DOCLING_OCR_AUTO=1            Auto-detect text layer per PDF; flip
                                do_ocr=True only when no text found.
                                Set to 0 to disable auto-detect.

Chunking is owned by ``wikify.ingest.hybrid_chunker.chunk_with_hybrid``
and runs on the persisted markdown; this module never produces chunks
itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..equations import _equation_id
from ._citations import bracketize_bare_refs
from ._sections import section_spans
from .registry import ParseResult, RawImage

_HF_PATCHED = False


# Layout/OCR batch sizes by total VRAM. Tuned so that 64-page tensors
# at images_scale=3.0 don't push layout activations + Granite-Docling
# KV cache past the VRAM ceiling. Anything past the last entry uses 64.
_GPU_BATCH_TIERS: tuple[tuple[int, int], ...] = (
    (8, 8),
    (16, 16),
    (32, 32),
)


def _gpu_batch_size_default() -> int:
    """Pick layout/ocr batch size from total VRAM.

    Returns the upstream Docling default (4) when CUDA is unavailable
    or the probe fails. On CUDA the result is one of 8/16/32/64
    depending on which ``_GPU_BATCH_TIERS`` row matches the device's
    total memory.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return 4
        gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    except (ImportError, RuntimeError, AttributeError, AssertionError):
        return 4
    for max_gb, batch_size in _GPU_BATCH_TIERS:
        if gb < max_gb:
            return batch_size
    return 64


# OOM-retry ladder for layout/OCR batch sizes. Highest-to-lowest so a
# linear scan finds the next-smaller value below the current setting.
# We only step batch size down on CUDA OOM; we never disable formulas,
# OCR, or any other quality-affecting knob — the contract is "fail loudly
# at batch=4 rather than silently degrade output".
_GPU_BATCH_RETRY_ORDER: tuple[int, ...] = (64, 32, 16, 8, 4)


def _is_cuda_oom(exc: BaseException) -> bool:
    """True if *exc* looks like a CUDA out-of-memory failure."""
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda" in msg


def _next_lower_batch(current: int) -> int | None:
    """Return the next value in ``_GPU_BATCH_RETRY_ORDER`` below *current*."""
    for b in _GPU_BATCH_RETRY_ORDER:
        if b < current:
            return b
    return None


def _convert_with_oom_retry(opts: DoclingOptions, path: Path):
    """Run ``converter.convert(path)``; on CUDA OOM step batch size down.

    Returns ``(result, effective_opts)`` so callers can read which
    batch size succeeded. Only ``layout_batch_size`` and
    ``ocr_batch_size`` are modified across retries — formulas, OCR,
    picture description, VLM, and ``images_scale`` are preserved
    exactly as configured. When even ``batch=4`` fails, the original
    OOM is re-raised wrapped in a clear message naming the corpus's
    fundamental VRAM ceiling so the operator knows to lower
    ``DOCLING_IMAGES_SCALE`` or move to a bigger GPU.
    """
    import copy

    effective = opts
    last_exc: BaseException | None = None
    while True:
        converter = _get_converter(effective)
        try:
            result = converter.convert(str(path.resolve()))
            return result, effective
        except RuntimeError as exc:
            if not _is_cuda_oom(exc):
                raise
            last_exc = exc
            current = max(
                effective.layout_batch_size, effective.ocr_batch_size,
            )
            next_batch = _next_lower_batch(current)
            if next_batch is None:
                raise RuntimeError(
                    f"docling CUDA OOM on {path.name} at batch={current}; "
                    f"lower DOCLING_IMAGES_SCALE or use a GPU with more VRAM"
                ) from last_exc
            sys.stderr.write(
                f"[docling] CUDA OOM on {path.name}, "
                f"retrying at batch={next_batch} (was {current})\n"
            )
            effective = copy.copy(effective)
            effective.layout_batch_size = next_batch
            effective.ocr_batch_size = next_batch


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

    formulas: bool = True
    formula_model: str = "granite_docling"  # "granite_docling" | "codeformulav2"
    ocr: bool = False
    ocr_auto: bool = True
    pic_classify: bool = False
    pic_describe: bool = False
    vlm: bool = False
    images_scale: float = 3.0
    # Per-stage batch sizes for GPU inference. PdfPipelineOptions exposes
    # these directly; raising layout/OCR batches gives the documented
    # up-to-6x speedup on Ampere+. The default factory probes the GPU
    # once and picks a tier (see ``_GPU_BATCH_TIERS``) so an 8 GB laptop
    # doesn't OOM-thrash and a 40 GB datacentre card still gets 64.
    # TableFormer is CPU-bound in current Docling, so the documented
    # safe value 4 stays.
    layout_batch_size: int = field(default_factory=_gpu_batch_size_default)
    ocr_batch_size: int = field(default_factory=_gpu_batch_size_default)
    table_batch_size: int = 4

    @classmethod
    def from_env(cls) -> DoclingOptions:
        """Build options from DOCLING_* environment variables.

        Layout and OCR batch sizes fall back to the VRAM-adaptive
        default (``_gpu_batch_size_default``) when the env var is
        unset OR empty. Explicit numeric overrides remain authoritative
        for users who know their VRAM headroom.
        """
        gpu_default = _gpu_batch_size_default()
        return cls(
            formulas=os.environ.get("DOCLING_FORMULAS", "1") != "0",
            formula_model=os.environ.get(
                "DOCLING_FORMULA_MODEL", "granite_docling",
            ),
            ocr=os.environ.get("DOCLING_OCR", "") == "1",
            ocr_auto=os.environ.get("DOCLING_OCR_AUTO", "1") != "0",
            pic_classify=os.environ.get("DOCLING_PIC_CLASSIFY", "") == "1",
            pic_describe=os.environ.get("DOCLING_PIC_DESCRIBE", "") == "1",
            vlm=os.environ.get("DOCLING_VLM", "") == "1",
            images_scale=float(os.environ.get("DOCLING_IMAGES_SCALE", "3.0")),
            layout_batch_size=int(
                os.environ.get("DOCLING_LAYOUT_BATCH_SIZE") or gpu_default,
            ),
            ocr_batch_size=int(
                os.environ.get("DOCLING_OCR_BATCH_SIZE") or gpu_default,
            ),
            table_batch_size=int(
                os.environ.get("DOCLING_TABLE_BATCH_SIZE", "4"),
            ),
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


def _disable_torch_compile_when_unsafe() -> None:
    """Disable torch.compile on Windows or CPU-only Docling runs.

    Triton is OpenAI's GPU compiler for fused kernels -- it only
    supports Linux. On CPU-only runs, compile warmup cost is also the
    wrong default for ingest because the pipeline should fail or proceed
    predictably, not spend minutes tracing model code before work starts.
    """
    global _DYNAMO_PATCHED
    if _DYNAMO_PATCHED:
        return
    if sys.platform != "win32" and _has_cuda():
        return
    _DYNAMO_PATCHED = True
    try:
        from docling.datamodel.settings import settings
        settings.inference.compile_torch_models = False
    except (ImportError, AttributeError):
        pass
    try:
        import torch._dynamo

        torch._dynamo.config.suppress_errors = True
    except ImportError:
        pass


def _disable_torch_compile_on_windows() -> None:
    """Backward-compatible wrapper for probe scripts."""
    _disable_torch_compile_when_unsafe()


_RUNTIME_CONFIGURED = False


def _configure_torch_runtime() -> None:
    """One-time torch + tokenizer thread setup.

    Caps intra-op threads at 4 so CPU-side BLAS doesn't saturate
    cores while GPU kernels are the actual bottleneck. Disables
    HuggingFace tokenizer fork-parallelism (default ON deadlocks on
    subprocess fork and clutters the log with warnings on Windows).
    Honours an explicit ``TOKENIZERS_PARALLELISM`` override.

    Must run before any tensor op or DocumentConverter
    instantiation, because ``torch.set_num_interop_threads`` raises
    once the thread pool is initialised. Sets the tokenizer env
    var first so it lands even if the torch import/setup fails.
    """
    global _RUNTIME_CONFIGURED
    if _RUNTIME_CONFIGURED:
        return
    _RUNTIME_CONFIGURED = True

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        import torch
        torch.set_num_threads(min(4, os.cpu_count() or 4))
        try:
            torch.set_num_interop_threads(min(2, os.cpu_count() or 2))
        except RuntimeError:
            # Pool already initialised (another importer touched
            # torch first); the intra-op cap above still applies.
            pass
    except (ImportError, RuntimeError):
        # Broken or already-constrained torch should not fail
        # ingest because of a performance guard.
        pass


_TEXT_LAYER_PROBE_PAGES = 3
_TEXT_LAYER_MIN_CHARS = 200


def _pdf_has_text_layer(path: Path) -> bool:
    """True if the PDF has an embedded text layer in its first pages.

    Born-digital PDFs always do; scanned PDFs don't (or have a tiny
    OCR-by-Acrobat layer that's still under the threshold). We probe
    the first few pages because that's enough to distinguish the two
    classes without paying full-document scan cost. Failure to open
    the file (corrupt, encrypted, non-PDF) returns ``True`` so we DO
    NOT flip OCR on speculatively — better to surface the upstream
    failure than to spend minutes OCRing a junk file.
    """
    try:
        import pymupdf
    except ImportError:
        return True
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return True
    try:
        chars = 0
        for page in doc[: _TEXT_LAYER_PROBE_PAGES]:
            chars += len((page.get_text() or "").strip())
            if chars >= _TEXT_LAYER_MIN_CHARS:
                return True
        return chars >= _TEXT_LAYER_MIN_CHARS
    finally:
        doc.close()


_CACHED_CONVERTER = None
_CACHED_OPTS_KEY = None


def _get_converter(opts: DoclingOptions):
    """Return a cached converter, rebuilding only if options changed.

    Cache key includes ``_has_cuda()`` because the converter's
    pipeline class differs between CUDA / CPU paths; if CUDA visibility
    flips mid-process (rare, but possible via env tweaks) we want a
    rebuild rather than a stale converter.
    """
    global _CACHED_CONVERTER, _CACHED_OPTS_KEY
    key = (
        opts.formulas, opts.formula_model, opts.ocr,
        opts.pic_classify, opts.pic_describe, opts.vlm,
        opts.images_scale,
        opts.layout_batch_size, opts.ocr_batch_size,
        opts.table_batch_size,
        _has_cuda(),
    )
    if _CACHED_CONVERTER is None or _CACHED_OPTS_KEY != key:
        _CACHED_CONVERTER = _build_converter(opts)
        _CACHED_OPTS_KEY = key
    return _CACHED_CONVERTER


def parse(
    path: Path,
    *,
    skip_metadata: bool = False,
    doc_cache_path: Path | None = None,
) -> ParseResult:
    """Parse one PDF / DOCX / PPTX / HTML via Docling.

    Returns a ``ParseResult`` with markdown + images + sections +
    metadata. Chunking is owned by the universal HybridChunker and
    runs on the persisted markdown; this function never produces
    chunks. ``skip_metadata=True`` skips ``assemble_pdf_metadata`` so
    the ingest DAG can fuse metadata in a later pass with DOI-resolved
    context.

    When ``doc_cache_path`` is set, the parsed ``DoclingDocument`` is
    saved as JSON to that path. The rechunk path can later load this
    cache via ``DoclingDocument.load_from_json`` and skip the
    markdown -> DoclingDocument re-parse, dropping ~75% of per-doc
    chunking cost.
    """
    _patch_hf_symlinks()
    _configure_torch_runtime()
    _disable_torch_compile_when_unsafe()

    opts = DoclingOptions.from_env()
    if (
        opts.formulas
        and path.suffix.lower() == ".pdf"
        and not _has_cuda()
        and os.environ.get("DOCLING_ALLOW_CPU_FORMULAS", "") != "1"
    ):
        raise RuntimeError(
            "Docling formula enrichment requires CUDA for practical ingest. "
            "Set DOCLING_ALLOW_CPU_FORMULAS=1 to run it on CPU, or use "
            "--parser lite for the lightweight no-enrichment parser path.",
        )

    # OCR auto-detect: born-digital PDFs already have a text layer,
    # so paying Docling's OCR pass is wasted minutes per doc. Probe
    # the first few pages and only flip do_ocr=True when we don't
    # find enough embedded text. PDFs only -- DOCX/PPTX/HTML have
    # native text and don't go through the OCR engine anyway.
    if (
        opts.ocr_auto
        and not opts.ocr
        and path.suffix.lower() == ".pdf"
        and not _pdf_has_text_layer(path)
    ):
        import copy
        opts = copy.copy(opts)
        opts.ocr = True
        sys.stderr.write(
            f"[docling] {path.name}: no text layer detected, "
            f"enabling OCR for this doc\n"
        )

    result, effective_opts = _convert_with_oom_retry(opts, path)
    doc = result.document

    md_text = doc.export_to_markdown()
    md_text = _light_clean(md_text, formulas_enabled=effective_opts.formulas)

    # Single linear pass over DoclingDocument items: collects
    # bibliography count, picture items, and formula items in one
    # iteration instead of three. Order matters because the ref-list
    # heuristic needs to know the last "references"/"bibliography"
    # header position.
    ref_count, images, formulas = _doc_walk(
        doc, want_formulas=effective_opts.formulas,
    )
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
    sections = section_spans(md_text)

    # Formulas come from the same _doc_walk pass above (when the
    # Granite-Docling head ran). Stash them on metadata so the
    # pipeline can merge them with markdown-regex equations.
    if effective_opts.formulas:
        metadata["_docling_formulas"] = formulas

    # Cache the DoclingDocument JSON so rechunk can skip
    # DocumentConverter on subsequent invocations.
    if doc_cache_path is not None:
        try:
            doc_cache_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save_as_json(doc_cache_path)
        except Exception as exc:
            sys.stderr.write(
                f"[docling] failed to cache DoclingDocument for "
                f"{path.name}: {exc}\n"
            )

    title = metadata.get("title") or path.stem

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
    """Standard pipeline options with enrichments.

    ``PdfPipelineOptions`` already exposes the per-stage batch knobs
    (``layout_batch_size``, ``ocr_batch_size``, ``table_batch_size``);
    ``ThreadedPdfPipelineOptions`` adds nothing on top, so we use the
    parent class unconditionally and pass the batch sizes regardless
    of CUDA availability — they're a no-op on CPU paths but shouldn't
    be silently dropped.
    """
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    kwargs: dict = {
        "accelerator_options": accel,
        "generate_picture_images": True,
        "images_scale": opts.images_scale,
        "do_ocr": opts.ocr,
        "do_formula_enrichment": opts.formulas,
        "do_picture_classification": opts.pic_classify,
        "do_picture_description": opts.pic_describe,
        "layout_batch_size": opts.layout_batch_size,
        "ocr_batch_size": opts.ocr_batch_size,
        # TableFormer is CPU-bound in current upstream Docling, so
        # ``table_batch_size`` is a soft hint. Keep at documented
        # default unless an upstream change moves it onto GPU.
        "table_batch_size": opts.table_batch_size,
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

    return PdfPipelineOptions(**kwargs)


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


def _doc_walk(
    doc, *, want_formulas: bool,
) -> tuple[int, list[RawImage], list[dict]]:
    """Single linear pass over a DoclingDocument's items.

    Returns ``(ref_count, images, formulas)`` where:

    - ``ref_count`` is the number of ListItems that appear after the
      last "References"/"Bibliography" SectionHeaderItem (used by
      ``bracketize_bare_refs`` for range validation; the heuristic
      avoids mistaking body-bullet lists for the bibliography);
    - ``images`` is every PictureItem whose rendered crop survives
      the ``_MIN_IMAGE_DIM`` filter, with caption + page metadata;
    - ``formulas`` is every FormulaItem with non-empty LaTeX text,
      keyed by ``_equation_id(f"display:{latex}")`` so it dedups
      cleanly against markdown-regex extracted equations downstream.
      ``want_formulas=False`` skips formula collection (caller knows
      the Granite-Docling head didn't run).

    Single iteration replaces three separate ``doc.iterate_items()``
    walks; on long PDFs each pass reconstructs prov + caption state,
    so the saving is real.
    """
    ref_count = 0
    images: list[RawImage] = []
    formulas: list[dict] = []
    try:
        from docling.datamodel.document import (
            ListItem,
            PictureItem,
            SectionHeaderItem,
        )
        from docling_core.types.doc.document import FormulaItem
    except ImportError:
        return ref_count, images, formulas

    in_ref_section = False
    counted_refs = 0
    for item, _level in doc.iterate_items():
        if isinstance(item, SectionHeaderItem):
            text = (getattr(item, "text", "") or "").lower()
            if "reference" in text or "bibliography" in text:
                in_ref_section = True
                counted_refs = 0
            else:
                in_ref_section = False
        elif in_ref_section and isinstance(item, ListItem):
            counted_refs += 1
        elif isinstance(item, PictureItem):
            img = _picture_to_raw_image(item, doc)
            if img is not None:
                images.append(img)
        elif want_formulas and isinstance(item, FormulaItem):
            latex = (getattr(item, "text", "") or "").strip()
            if latex:
                page = None
                prov = getattr(item, "prov", None)
                if prov:
                    page = getattr(prov[0], "page_no", None)
                label = (getattr(item, "label", "") or "")
                formulas.append({
                    "id": _equation_id(f"display:{latex}"),
                    "latex": latex,
                    "label": str(label) if label else "",
                    "type": "display",
                    "kind": "display",
                    "page": page,
                    "context": "",
                    "char_offset": -1,
                })
    ref_count = counted_refs
    return ref_count, images, formulas


def _picture_to_raw_image(item, doc) -> RawImage | None:
    """Extract a single PictureItem to a ``RawImage`` or skip on tiny size.

    Mirrors what the legacy ``_extract_images`` did per item; broken
    out so ``_doc_walk`` and the public ``_extract_images`` wrapper
    can share the conversion logic.
    """
    import io as _io

    caption = ""
    if hasattr(item, "caption_text"):
        caption = item.caption_text(doc) or ""

    page = None
    if hasattr(item, "prov") and item.prov:
        page = item.prov[0].page_no

    data = _image_bytes_from_item(item)
    if data is None:
        return None

    try:
        from PIL import Image as PilImage  # noqa: N813

        pil = PilImage.open(_io.BytesIO(data))
        w, h = pil.size
        if w < _MIN_IMAGE_DIM and h < _MIN_IMAGE_DIM:
            return None
    except Exception:
        pass

    content_hash = hashlib.sha1(data).hexdigest()[:12]
    return RawImage(
        data=data,
        ext="png",
        caption=caption,
        page=page,
        content_hash=content_hash,
    )


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
    """Extract images from a DoclingDocument (back-compat shim).

    Internal callers go through ``_doc_walk`` for a single-pass
    collection of refs + images + formulas. This wrapper exists so
    external probe scripts and tests that imported
    ``_extract_images`` keep working.
    """
    _ref_count, images, _formulas = _doc_walk(doc, want_formulas=False)
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


def extract_formulas(doc) -> list[dict]:
    """Public wrapper: structural FormulaItem extraction (back-compat).

    Internal ingest goes through ``_doc_walk`` so refs + images +
    formulas are collected in a single pass over ``doc.iterate_items()``.
    Probe scripts and tests that import ``extract_formulas`` standalone
    keep working through this wrapper.
    """
    _ref_count, _images, formulas = _doc_walk(doc, want_formulas=True)
    return formulas
