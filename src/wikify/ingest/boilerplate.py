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
    # Publisher article-recommendation widgets that contaminate top-N
    # retrieval. Audit-flagged: chunk:380eb7a2 had its body inside a
    # section_path starting with "Articles You May Be Interested In".
    r"articles you may be interested in",
    r"recommended (by|for you)",
    r"cited by\s*$",
    # Page-footer download stamps the parser leaves inline ("Downloaded
    # from https://onlinelibrary.wiley.com/... 15 March 2026 12:11:38").
    r"downloaded\s+from\s+https?://",
)
_BOILERPLATE_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in BOILERPLATE_MARKERS)

# Patterns that, even when they appear ONCE in a short chunk, mark it as
# header/footer/admin boilerplate. These are unambiguous metadata markers
# — finding one of them in the leading text of a chunk is enough.
DEFINITIVE_BOILERPLATE_MARKERS: tuple[str, ...] = (
    # DoD / DTIC standard cover-sheet (SF298) preamble.
    r"\bForm\s+Approved\s+OMB\s+No\.",
    r"\bpublic\s+reporting\s+burden\b",
    # Keyword block at the head of a paper.
    r"^\s*Keywords?\s*:\s*\S",
    r"^\s*A\s*R\s*T\s*I\s*C\s*L\s*E\s+I\s*N\s*F\s*O\b",
    # Pure ISSN / DOI / journal-homepage banners (short metadata chunks).
    r"^\s*ISSN[:\s-]+\d{4}\b",
    r"^\s*DOI\s*:\s*10\.\d{4,9}/",
    r"\bjournal\s+homepage\s*:",
    # Copyright / © / rights-reserved one-liners.
    r"^\s*©\s*\d{4}",
    r"^\s*Copyright\s+©?\s*\d{4}",
    # Numbered references-list dumps that slipped past section_classifier.
    # Match three or more consecutive numbered entries in one chunk.
    r"(?:^|\n)\s*[\(\[]\d+[\)\]]\s+[A-Z][a-zA-Z\-]+,\s+[A-Z]\.[A-Z\.\s]*?[;,]"
    r"[\s\S]{0,400}?"
    r"(?:^|\n)\s*[\(\[]\d+[\)\]]\s+[A-Z][a-zA-Z\-]+,",
)
_DEFINITIVE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE | re.MULTILINE) for p in DEFINITIVE_BOILERPLATE_MARKERS
)

# Patterns that are unambiguous metadata markers ONLY in ALL-CAPS form;
# the corresponding lowercase / mixed-case phrasing is a legitimate prose
# construction (e.g. "Edited by the journal editorial team, this special
# issue ..." or "The PI received 25 March 2024 funding ... and accepted
# 10 May 2024 collaboration ..."). Compiled WITHOUT ``re.IGNORECASE`` so
# the case requirement is structural.
DEFINITIVE_BOILERPLATE_MARKERS_CASE_SENSITIVE: tuple[str, ...] = (
    # Journal "Article history / Received / Accepted / Available online".
    # Always Capitalized in real journal metadata; lowercase forms like
    # "the PI received 25 March 2024 funding ..." are legitimate prose.
    # Anchored to start-of-line (with MULTILINE flag) so a Title-Case
    # word appearing mid-prose ("The lab received 25 March 2024 the new
    # reagent ...") does not false-positive on the all-caps-vs-prose
    # boundary.
    r"\bArticle\s+history\b",
    r"^\s*Received\s*:?\s*\d?\d\s+\w+\s+\d{4}\b",
    r"^\s*Accepted\s*:?\s*\d?\d\s+\w+\s+\d{4}\b",
    r"^\s*Available\s+online\s+\d",
    # Frontiers-style editorial-board header blocks at the top of an article.
    # Calibrated against the Kumar 2025 (Front. Nanotechnol.) chunk c0000
    # which is verbatim "EDITED BY\nCarlo Ricciardi,...\nREVIEWED BY\n...".
    r"^\s*EDITED\s+BY\b",
    r"^\s*REVIEWED\s+BY\b",
    # Multi-stage publication-history paragraph (the all-caps Frontiers form
    # "RECEIVED 02 May 2025 ACCEPTED 10 June 2025 PUBLISHED 19 June 2025").
    # The existing single-stage "Received: DD Month YYYY" pattern above
    # already catches the colon form; this covers the run-on variant.
    # Each marker must be followed by a "DD Month YYYY" date so prose like
    # "the film received post-anneal ... was accepted ... was published" does
    # not false-positive.
    r"\bRECEIVED\b\s*\d?\d\s+\w+\s+\d{4}[\s\S]{0,200}"
    r"\bACCEPTED\b\s*\d?\d\s+\w+\s+\d{4}[\s\S]{0,200}\bPUBLISHED\b",
    # CS1-style citation header that IS the chunk (Kumar 2025 c0002 verbatim
    # "Kumar S, Yadav D, Stathopoulos S and Prodromakis T (2025) Performance
    # ... Front. Nanotechnol. 7:1621554. doi: 10.3389/fnano.2025.1621554").
    # Anchored with ``\A`` so the citation must start the chunk, and ending
    # with the doi suffix followed by only whitespace / end-of-chunk so
    # inline mid-sentence citations like "As shown by Smith J (2021) ...
    # doi: 10.1038/x. Our work extends ..." do not trip.
    r"\A\s*[A-Z][A-Za-z\-]+\s+[A-Z](?:[A-Za-z\.\s,]+?)\(\d{4}\)\s+[^.]{5,300}\."
    r"\s+(?:[A-Z][A-Za-z]*\.?\s*){1,6}\d+(?::\d+)?[.;]\s*doi\s*:\s*10\.\S+\s*\Z",
)
_DEFINITIVE_PATTERNS_CASE_SENSITIVE = tuple(
    re.compile(p, re.MULTILINE) for p in DEFINITIVE_BOILERPLATE_MARKERS_CASE_SENSITIVE
)

# A chunk this long is almost certainly substantive content even if it
# contains a few boilerplate phrases (e.g., a long review paper section
# that quotes a license notice). Above this floor, never flag.
BOILERPLATE_MAX_WORDS = 600

# Number of distinct non-overlapping marker spans required to flag.
BOILERPLATE_MIN_HITS = 2


# Section-path keywords that, on their own, mark the chunk as
# publisher-sidebar boilerplate even when the body text is short and
# wouldn't trip the marker-density test. These match against any
# section_path element; the case-insensitive substring is enough.
SECTION_PATH_BOILERPLATE_KEYWORDS: tuple[str, ...] = (
    "articles you may be interested",
    "recommended by acs",
    "recommended by springer",
    "recommended for you",
    "see also",
    "related content",
)


def _section_path_is_boilerplate(section_path: list[str] | None) -> bool:
    if not section_path:
        return False
    for element in section_path:
        if not element:
            continue
        low = element.lower()
        for kw in SECTION_PATH_BOILERPLATE_KEYWORDS:
            if kw in low:
                return True
    return False


def is_boilerplate(text: str, section_path: list[str] | None = None) -> bool:
    """True when the chunk is admin / legal / publisher-sidebar boilerplate.

    Two paths fire:

    1. Section-path fast path -- when any heading in ``section_path``
       matches a publisher-sidebar keyword (Articles You May Be
       Interested In, Recommended by ACS, etc.), the chunk is
       boilerplate regardless of body length. This is what catches
       audit chunk:380eb7a2.
    2. Body-text marker density -- counts UNIQUE non-overlapping
       marker spans. The phrase "licensed under a Creative Commons
       Attribution" matches multiple markers; span-dedup keeps that
       as one signal. Triggers when at least ``BOILERPLATE_MIN_HITS``
       distinct spans match AND the chunk is short enough that the
       density is meaningful.
    """
    if _section_path_is_boilerplate(section_path):
        return True
    # Definitive single-hit patterns: scoped to the chunk's leading region
    # (first ~600 chars) so a passing mention in the middle of a long body
    # chunk does not trigger. The patterns themselves are unambiguous.
    head = text[:600]
    for pattern in _DEFINITIVE_PATTERNS:
        if pattern.search(head):
            return True
    for pattern in _DEFINITIVE_PATTERNS_CASE_SENSITIVE:
        if pattern.search(head):
            return True
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
