"""Configuration constants for wikify. Pure values, no imports."""

# -- token estimation --------------------------------------------------------
CHARS_PER_TOKEN = 4  # rule-of-thumb for English prose (~10% accurate)

# -- cost meter --------------------------------------------------------------
ABORT_RATIO = 1.05  # hard-abort at this multiple of budget target

# Tier pricing in haiku-equivalent units (per-token cost, normalized to Haiku = 1).
#
# Reference pricing (Claude 4.5 / 4.6 family, as of 2026-04):
#   Haiku  4.5: $1  / MTok input, $5  / MTok output
#   Sonnet 4.5: $3  / MTok input, $15 / MTok output
#   Opus   4.6: $15 / MTok input, $75 / MTok output
#
# The INPUT/OUTPUT multipliers below normalize each tier's per-token cost to
# Haiku's per-token cost. Haiku's own output/input ratio (5x) is NOT folded in
# — output tokens are counted at the tier's output multiplier separately.
#
# Haiku  (S): input 1x, output 5x baseline
# Sonnet (M): input 3x, output 15x -> 3x Haiku on both
# Opus   (L): input 15x, output 75x -> 15x Haiku on both
TIER_S_INPUT = 1.0
TIER_S_OUTPUT = 5.0
TIER_S_OVERHEAD = 50.0
TIER_M_INPUT = 3.0
TIER_M_OUTPUT = 15.0
TIER_M_OVERHEAD = 100.0
TIER_L_INPUT = 15.0
TIER_L_OUTPUT = 75.0
TIER_L_OVERHEAD = 300.0

# -- dispatch ----------------------------------------------------------------
# 30 min: absorbs slow handler runs (Claude Code session credit drain,
# heavy chunks needing multiple verifier iterations) without aborting
# the harness mid-batch. The harness re-runs from scratch on each
# invocation; cheap timeouts cost a full restart.
DISPATCH_TIMEOUT = 1800.0  # seconds to wait for a response file
POLL_INTERVAL = 0.05  # seconds between polls for response file

# -- explorer ----------------------------------------------------------------
CHUNKS_PER_LANDED_DOC = 3  # chunks sampled per global-jump document landing

# -- budget ------------------------------------------------------------------
CURATE_FRACTION = 0.05  # fraction of total budget reserved for curation
NOVELTY_THRESHOLD = 0.05  # dN/dC below which adaptive schedule shifts to write

# -- query -------------------------------------------------------------------
MAX_CANDIDATES = 12  # max wiki pages considered per query
BODY_EXCERPT_CHARS = 600  # characters of page body included in evidence
