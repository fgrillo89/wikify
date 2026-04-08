"""Tokeniser-agnostic token counting for context budgeting.

v1 uses a 4-characters-per-token rule of thumb. This is accurate within
~10% for English prose and is enough for envelope accounting (the cap is
a ceiling, not a precise meter). When/if we need exact counts for cost
telemetry, swap the implementation here for tiktoken in one place.
"""

from __future__ import annotations

_CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in `text`.

    Returns 0 for empty input. The estimate is a rule of thumb; callers
    that need exact counts should not use this function.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)
