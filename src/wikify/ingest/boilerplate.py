"""Soft boilerplate detection for chunks.

A chunk is "boilerplate" when its content is dominated by admin / legal /
journal-end-matter language rather than substantive prose. Examples:

  - thesis copyright preambles ("Copyright and Moral Rights for this thesis...")
  - Nature-family end-matter ("Supplementary information... Correspondence
    and requests for materials...")
  - bibliographic citation blocks
  - "View article online", "How to cite", subscription notices

The predicate is intentionally conservative: it requires at least 2 distinct
non-overlapping marker spans AND the chunk must be reasonably short (< 600
words). A real abstract that mentions "Copyright 2024" once at the bottom
won't trip; a chunk that's mostly legal text will.

Boilerplate detection is consumed in two places:

  1. Ingest sets ``Chunk.is_boilerplate`` once per chunk.
  2. The fluent API (``KnowledgeGraph.chunks(...)``) filters out
     ``is_boilerplate=True`` chunks by default. Callers that want to see
     them pass ``include_boilerplate=True``.

Single source of truth: this module owns the predicate. Other code uses
the persisted ``Chunk.is_boilerplate`` flag, never re-runs the predicate.
"""

from __future__ import annotations

import re

# Phrases that, when MULTIPLE distinct spans co-occur in a short chunk,
# indicate the chunk is admin/legal/metadata rather than content. Each
# entry is a regex pattern (case-insensitive). Order does not matter.
#
# Tuning notes:
#   - Generic phrases like "creative commons" and "licensed under" were
#     deliberately excluded — they fire on standard CC-BY footers
#     appended to real abstracts in many open-access papers, producing
#     false positives.
#   - Span dedup is applied below so "licensed under a Creative Commons"
#     style overlap doesn't double-count.
BOILERPLATE_MARKERS: tuple[str, ...] = (
    r"all rights reserved",
    r"moral rights",
    r"copyright (holders?|owners?|holder\W*s?)",
    r"copyright\s+\W?\s*\d{4}.{0,80}(reserved|reprint)",
    r"reprints?\s+and\s+permissions?",
    r"for personal noncommercial",
    r"this (thesis|paper|article|content) (cannot|must not|may not|shall not)",
    r"obtain(ing)?\s+permission in writing",
    r"cite this (article|paper):",
    r"view (the )?article online",
    r"how to cite",
    r"university repository",
    r"bibliographic details must be given",
    r"institutional access",
    r"^subscriber\s",
    r"peer review information",
    r"correspondence and requests for materials",
    r"supplementary information.{0,80}online version",
)
_BOILERPLATE_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in BOILERPLATE_MARKERS)

# A chunk this long is almost certainly substantive content even if it
# contains a few boilerplate phrases (e.g., a long review paper section
# that quotes a license notice). Above this floor, never flag.
BOILERPLATE_MAX_WORDS = 600

# Number of distinct non-overlapping marker spans required to flag.
BOILERPLATE_MIN_HITS = 2


def is_boilerplate(text: str) -> bool:
    """True when the chunk text is dominated by legal/metadata boilerplate.

    Counts UNIQUE non-overlapping match spans, not pattern hits. The
    phrase "licensed under a Creative Commons Attribution" would match
    several markers if both `creative commons` and `licensed under`
    were in the marker set — span-dedup ensures that's one signal,
    not two. Only triggers when at least ``BOILERPLATE_MIN_HITS``
    distinct spans match AND the chunk is short enough that the
    boilerplate density is meaningful.
    """
    if len(text.split()) > BOILERPLATE_MAX_WORDS:
        return False
    spans: list[tuple[int, int]] = []
    for pattern in _BOILERPLATE_PATTERNS:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))
    if len(spans) < BOILERPLATE_MIN_HITS:
        return False
    spans.sort()
    distinct = 0
    last_end = -1
    for s, e in spans:
        if s >= last_end:
            distinct += 1
            last_end = e
    return distinct >= BOILERPLATE_MIN_HITS
