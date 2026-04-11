"""Distill pipeline configuration constants."""

# -- sampler -----------------------------------------------------------------
CHUNKS_PER_LANDED_DOC = 3  # chunks sampled per global-jump document landing

# -- schedule ----------------------------------------------------------------
CURATE_FRACTION = 0.05  # fraction of total budget reserved for curation
NOVELTY_THRESHOLD = 0.05  # dN/dC below which adaptive schedule shifts to write

# -- query -------------------------------------------------------------------
MAX_CANDIDATES = 12  # max wiki pages considered per query
BODY_EXCERPT_CHARS = 600  # characters of page body included in evidence
