"""Ingest pipeline configuration constants."""

# -- section filtering -------------------------------------------------------
# Section types that carry no extractable knowledge. Used by the explorer
# index builder to exclude chunks and by distill to skip at extraction time.
# Centralised here so ingest and distill stay in sync.
SKIP_SECTION_TYPES: frozenset[str] = frozenset(
    {"references", "acknowledgments", "appendix"}
)

# -- chunking ----------------------------------------------------------------
# Section-level strategy: emit each section as a single chunk when it fits the
# embedder's context window. Long-context embedders (nomic v1.5 has an 8192-
# token window) make the old 400-token paragraph-level chunks counterproductive
# -- arguments get split across neighbours and retrieval has to stitch them
# back together. With section-as-chunk, one hit = one coherent section.
TARGET_CHUNK_CHARS = 8000  # ~2000 tokens — keep whole sections when possible
MIN_CHUNK_CHARS = 200  # minimum chunk size before flush
OVERLAP_CHARS = 0  # section chunks carry their own context; no overlap needed


def max_chunk_chars() -> int:
    """Hard ceiling on chunk size, derived from the active embedder's window.

    Reads ``current_backend()`` so the cap tracks whichever model is loaded.
    Clamps to 6000 tokens so a pathologically long section does not produce
    a single 15k+ character chunk; 2.5 chars/tok is the worst-case estimate
    for reference-heavy academic text.
    """
    from ..embedding import current_backend, model_config

    backend = current_backend()
    cfg = model_config(backend.get("model"))
    return int(min(cfg.max_tokens, 6000) * 2.5)
# Drop chunks whose stripped text has fewer than this many alphanumeric
# characters. Catches markdown-format-noise chunks like ``"##"`` or
# ``"**\n\n## _"`` that survive the parse but carry zero information.
MIN_CHUNK_ALNUM = 30

# -- figure extraction -------------------------------------------------------
MAX_MEDIA_PER_PAPER = 80  # cap images extracted per PDF
MIN_IMG_WIDTH = 100  # minimum image width in pixels
MIN_IMG_HEIGHT = 100  # minimum image height in pixels
MIN_IMG_BYTES = 2000  # minimum raw image size in bytes
SCAN_THRESHOLD = 15  # images-per-page above which page is treated as scanned

# -- document similarity ---------------------------------------------------
DOC_SIM_COS = 0.75  # cosine threshold for doc-level similarity edges
