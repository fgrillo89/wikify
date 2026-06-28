"""Configuration constants for wikify. Pure values, no imports."""

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
