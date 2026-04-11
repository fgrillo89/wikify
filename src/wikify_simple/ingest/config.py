"""Ingest pipeline configuration constants."""

# -- chunking ----------------------------------------------------------------
TARGET_CHUNK_CHARS = 1600  # target chunk size (~400 tokens)
MIN_CHUNK_CHARS = 200  # minimum chunk size before flush
OVERLAP_CHARS = 200  # inter-chunk overlap (~50 tokens)

# -- figure extraction -------------------------------------------------------
MAX_MEDIA_PER_PAPER = 80  # cap images extracted per PDF
MIN_IMG_WIDTH = 100  # minimum image width in pixels
MIN_IMG_HEIGHT = 100  # minimum image height in pixels
MIN_IMG_BYTES = 2000  # minimum raw image size in bytes
SCAN_THRESHOLD = 15  # images-per-page above which page is treated as scanned

# -- corpus graph ------------------------------------------------------------
KNN_K = 10  # neighbours per chunk in kNN similarity edges
STRONG_COS = 0.75  # cosine threshold for strong similarity edges
DOC_SIM_COS = 0.75  # cosine threshold for doc-level similarity edges
