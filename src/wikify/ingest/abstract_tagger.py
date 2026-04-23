"""Canonical-abstract identification at ingest time.

Sets ``Chunk.section_type = "abstract"`` on **exactly one** chunk per doc
that has body content. This makes "the abstract" a queryable invariant
on the data layer rather than a per-consumer ad-hoc heuristic.

Selection algorithm (see ``find_canonical_abstract``):

  1. **Classifier**: a chunk already tagged ``section_type == "abstract"``
     by :func:`section_classifier.classify_section_path` and clearing
     the ``ABSTRACT_MIN_WORDS`` floor. Trust the heading-based tag.
  2. **Fuzzy heading match in window**: a chunk whose deepest section
     heading matches ``ABSTRACT_VOCAB`` (``abstract``, ``summary``,
     ``executive summary``, ``tl;dr``, ``in brief``, ``highlights``)
     within the first 1/3 of the doc's text and within
     ``[ABSTRACT_MIN_WORDS, ABSTRACT_MAX_WORDS]`` words.
  3. **First substantive in window**: first chunk in ``ord`` order
     within the first 1/3 of the doc's text with at least
     ``ABSTRACT_MIN_WORDS`` words.
  4. **First substantive in doc**: same threshold, but anywhere in
     the doc. Catches papers where heavy front-matter pushes the
     abstract beyond the first 1/3 window.
  5. **Longest in doc**: last resort, guarantees one tag per doc with
     any usable chunk.

Boilerplate chunks (``is_boilerplate=True``) are skipped at every walk
step. They live in the corpus but are never picked as the canonical
abstract.

The tagger is **idempotent**: re-running clears any prior abstract tags
and re-applies the rule. Safe to call on every refresh.
"""

from __future__ import annotations

import re

from ..models import Chunk
from .config import SKIP_SECTION_TYPES

# Word-count window for what counts as a real abstract.
ABSTRACT_MIN_WORDS = 100
ABSTRACT_MAX_WORDS = 1000

# Section heading vocabulary recognised as an abstract / summary.
# Narrow on purpose: ``overview`` and ``synopsis`` are too common in
# paper titles ("...An Overview") and intra-paper "Overview of X"
# sections to use as signals.
ABSTRACT_VOCAB: tuple[str, ...] = (
    "abstract",
    "summary",
    "executive summary",
    "tl;dr",
    "tldr",
    "in brief",
    "highlights",
)
_ABSTRACT_RE = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in ABSTRACT_VOCAB) + r")\b",
    re.IGNORECASE,
)
# A "Graphical / Visual / Video Abstract" heading is usually a tiny
# caption-like blob, not the canonical abstract — exclude.
_DOWNGRADE_RE = re.compile(r"\b(graphical|visual|video)\s+abstract\b", re.IGNORECASE)

_SECTION_TYPE_ABSTRACT = "abstract"


def tag_abstracts(chunks: list[Chunk]) -> None:
    """Mutate ``chunks`` so each doc has exactly one canonical abstract.

    Groups chunks by ``doc_id``, clears any pre-existing abstract tags,
    and re-applies the selection rule per doc. Idempotent.

    Designed to run AFTER ``classify_section_path`` has populated each
    chunk's ``section_type`` and AFTER ``boilerplate.is_boilerplate``
    has set the ``is_boilerplate`` flag on each chunk.
    """
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)

    for doc_chunks in by_doc.values():
        # 1. Clear stale tags so the result reflects the current rule.
        prior_abstract_chunks: list[Chunk] = []
        for c in doc_chunks:
            if c.section_type == _SECTION_TYPE_ABSTRACT:
                prior_abstract_chunks.append(c)

        pick = find_canonical_abstract(doc_chunks)
        # 2. Strip prior tags from any chunk that is NOT the new pick.
        for c in prior_abstract_chunks:
            if pick is None or c.id != pick.id:
                c.section_type = "body"  # demote to body
        # 3. Apply the new tag (if a pick was found).
        if pick is not None:
            pick.section_type = _SECTION_TYPE_ABSTRACT


def find_canonical_abstract(chunks_for_doc: list[Chunk]) -> Chunk | None:
    """Return the canonical abstract chunk for one doc, or None.

    See module docstring for the 5-tier selection algorithm. Returns
    None only when the doc has no usable chunks at all (every chunk is
    a skip-type or caption — which means the doc has no body content).
    """
    usable = [
        c for c in chunks_for_doc
        if c.section_type not in SKIP_SECTION_TYPES
        and not (c.section_path and c.section_path[0] == "__image__")
    ]
    if not usable:
        return None
    usable.sort(key=lambda c: c.ord)

    # Soft boilerplate filter: skip chunks dominated by legal/metadata
    # phrases. They live in the corpus but are never picked as the
    # canonical abstract. Falls back to the full set if EVERYTHING is
    # boilerplate (so the longest-overall last-resort still rescues us).
    non_bp = [c for c in usable if not c.is_boilerplate]
    if not non_bp:
        non_bp = usable
    walked = non_bp

    # Window = first 1/3 of doc text by word count, with a 3-chunk
    # minimum so very short docs aren't restricted to a 1-chunk window.
    total_words = sum(_word_count(c.text) for c in walked)
    third = total_words / 3.0
    window: list[Chunk] = []
    cum = 0
    for c in walked:
        window.append(c)
        cum += _word_count(c.text)
        if cum >= third:
            break
    while len(window) < min(3, len(walked)):
        window.append(walked[len(window)])

    # Tier 1: classifier-tagged abstract chunk anywhere within range.
    for c in walked:
        if (
            c.section_type == _SECTION_TYPE_ABSTRACT
            and ABSTRACT_MIN_WORDS <= _word_count(c.text) <= ABSTRACT_MAX_WORDS
        ):
            return c

    # Tier 2: fuzzy heading match in window + word range.
    for c in window:
        if (
            _is_abstract_heading(c)
            and ABSTRACT_MIN_WORDS <= _word_count(c.text) <= ABSTRACT_MAX_WORDS
        ):
            return c

    # Tier 3: first chunk in window with >= MIN_WORDS.
    for c in window:
        if _word_count(c.text) >= ABSTRACT_MIN_WORDS:
            return c

    # Tier 4: first chunk anywhere in doc with >= MIN_WORDS.
    for c in walked:
        if _word_count(c.text) >= ABSTRACT_MIN_WORDS:
            return c

    # Tier 5: longest chunk in doc. Guarantees one tag per body-bearing doc.
    return max(walked, key=lambda c: _word_count(c.text))


def _word_count(text: str) -> int:
    return len(text.split())


def _is_abstract_heading(chunk: Chunk) -> bool:
    """Does the chunk's leaf section heading match abstract vocabulary?

    Checks ONLY the deepest section heading element. ``section_path``
    often includes the paper title as element 0 (e.g., a paper titled
    "...An Overview" would otherwise false-match), so single-element
    paths with a long leaf are rejected.
    """
    sp = chunk.section_path or []
    if not sp:
        return False
    leaf = sp[-1]
    if _DOWNGRADE_RE.search(leaf):
        return False
    if not _ABSTRACT_RE.search(leaf):
        return False
    # Singleton paths are usually the doc title; only accept short clean
    # leaf headings ("Abstract", "Executive Summary", not full titles).
    if len(sp) == 1 and len(leaf.split()) > 6:
        return False
    return True
