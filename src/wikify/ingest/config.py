"""Ingest pipeline configuration constants."""

# -- section filtering -------------------------------------------------------
# Section types that carry no extractable knowledge. Used by the explorer
# index builder to exclude chunks and by distill to skip at extraction time.
# Centralised here so ingest and distill stay in sync.
SKIP_SECTION_TYPES: frozenset[str] = frozenset(
    {"references", "acknowledgments", "appendix"}
)

# -- chunking ----------------------------------------------------------------
TARGET_CHUNK_CHARS = 1600  # target chunk size (~400 tokens)
MIN_CHUNK_CHARS = 200  # minimum chunk size before flush
OVERLAP_CHARS = 200  # inter-chunk overlap (~50 tokens)
# Hard ceiling on chunks the embedder will see. The default fastembed
# model (``sentence-transformers/all-MiniLM-L6-v2``) has a 512-token
# input window; chunks above the cap are silently truncated by the
# tokenizer and lose information past the limit. The chunker enforces
# a soft cap of ~480 chars-per-token-budget * 4 chars/token, then
# splits any oversize residual at sentence boundaries.
MAX_CHUNK_TOKENS = 450  # safety margin under the 512-token model max
# Conservative chars→tokens estimate. Body prose averages ~4 chars/tok but
# references (author lists, abbreviations, journal initials) pack ~2.0
# chars/tok. We use 2.5 so the worst-case academic-text format still fits.
MAX_CHUNK_CHARS = int(MAX_CHUNK_TOKENS * 2.5)  # ~1125 chars
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
