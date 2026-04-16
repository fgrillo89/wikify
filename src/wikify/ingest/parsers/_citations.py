"""Shared citation-marker restoration for PDF parsers.

PDF layout tools strip inline citation brackets. Docling loses the
``[N]`` around superscripted numerals; Marker sometimes keeps
``<sup>N</sup>`` HTML and sometimes concatenates the numbers onto the
preceding word (``switches20-22``). Downstream code (``graph.py``,
``parse_citation_markers``) only understands bracketed ``[N]`` form,
so parsers must normalize before the markdown is persisted.

This module owns that normalization.

Pipeline:

    ref_count = count_ref_list_items_from_md(md)
    md = bracketize_sup_refs(md)            # <sup>2-19</sup> -> [2-19]
    md = bracketize_concat_refs(md, ref_count=ref_count)
                                            # switches20-22 -> switches [20-22]
    md = bracketize_bare_refs(md, ref_count=ref_count)
                                            # word 20-22.   -> word [20-22].

The bare-refs and concat passes both validate numbers against the
bibliography length so raw prose numbers (``100 nm``, ``300 K``) are
not accidentally bracketed.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

# Unit suffixes that mark a prose number, not a citation.
_UNITS = frozenset({
    "nm", "um", "mm", "cm", "m", "km",
    "mv", "kv", "ma", "ka", "mhz", "ghz", "thz",
    "ev", "mev", "kev",
    "k", "c", "v", "a", "w", "s", "ms", "ns", "ps",
    "hz", "ohm", "db",
    "at", "wt", "mol", "torr", "pa", "mpa", "gpa",
    "min", "max",
})

# Words that, when preceding a number, mark a prose measurement.
_MEAS_WORDS = frozenset({
    "is", "was", "are", "of", "about", "approximately", "nearly",
    "over", "under", "than", "to", "from", "between", "at",
    "x", "by", "or", "and", "only",
})


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


_SUP_RE = re.compile(
    r"<sup>\s*(?P<body>\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*)\s*</sup>",
    re.IGNORECASE,
)


def bracketize_sup_refs(md: str) -> str:
    """Convert ``<sup>N</sup>``-style citation markup to ``[N]``.

    Only fires when the tag body is a pure numeric run (single, comma
    list, or hyphen range). Superscripts carrying letters or other
    markup (``<sup>a</sup>`` affiliations, ``<sup>o</sup>`` degree
    symbols) are left untouched.
    """
    if not md or "<sup>" not in md.lower():
        return md

    def _sub(m: re.Match) -> str:
        nums = re.sub(r"\s+", "", m.group("body"))
        return f"[{nums}]"

    return _SUP_RE.sub(_sub, md)


# Word+digits with a range/list signature. Triggers on "switches20-22"
# and "devices2,3,4" but NOT on single numeric suffixes like "RAM32"
# that are too ambiguous with model/version names. The 4-alpha minimum
# keeps chemistry tokens (``CO2``, ``H2O``) out of the match.
_CONCAT_RE = re.compile(
    r"(?P<pre>[A-Za-z]{4,})"
    r"(?P<nums>\d{1,3}(?:[,\u2013-]\d{1,3})+)"
    r"(?=[.,;)\s])"
)


def bracketize_concat_refs(md: str, *, ref_count: int) -> str:
    """Bracket word-concatenated citation runs (``switches20-22`` -> ``switches [20-22]``).

    Marker sometimes concatenates inline references onto the previous
    word. The pattern requires a 4+ letter prefix and a multi-number
    run (range or comma list) before punctuation/whitespace; both
    constraints together are strongly citation-shaped, so this pass
    brackets directly instead of leaving the result to
    ``bracketize_bare_refs`` (which would skip the match when followed
    by a lowercase continuation word).

    Numbers are still validated against ``ref_count`` so out-of-range
    ranges (``switches100-200`` when the paper only has 30 refs) stay
    untouched.
    """
    if not md or ref_count < 2:
        return md

    def _replace(m: re.Match) -> str:
        nums_str = m.group("nums")
        parts = re.split(r"[,\u2013-]", nums_str)
        try:
            nums = [int(p.strip()) for p in parts if p.strip()]
        except ValueError:
            return m.group(0)
        if not nums or any(n < 1 or n > ref_count for n in nums):
            return m.group(0)
        return f"{m.group('pre')} [{nums_str}]"

    return _CONCAT_RE.sub(_replace, md)


_REF_RE = re.compile(
    r"(?P<pre>\w+) (?P<nums>\d{1,3}(?:[,\u2013-]\d{1,3})*)(?P<post>[.,;) ])"
)


def bracketize_bare_refs(md: str, *, ref_count: int) -> str:
    """Wrap bare inline reference numbers in ``[N]`` brackets.

    Docling strips bracket formatting from superscript citations,
    leaving bare ``20-22`` instead of ``[20-22]``. This post-processor
    restores brackets so the citation ordinal resolver can match them.

    Conservative heuristics to avoid corrupting normal numbers:
    - Only runs when the document has a detectable references section
      (``ref_count > 0``), so we know what range is valid
    - Numbers must be in [1, ref_count] range
    - Must appear as comma/hyphen-separated groups immediately before
      sentence-ending punctuation (``.``, ``,``, ``;``)
    - Must NOT be followed by a unit (nm, K, V, mA, etc.)
    - Must NOT be preceded by common measurement words
    """
    if not md or ref_count < 2:
        return md

    def _replace(m: re.Match) -> str:
        pre_word = m.group("pre").lower()
        nums_str = m.group("nums")
        post = m.group("post")

        if pre_word in _MEAS_WORDS:
            return m.group(0)

        parts = re.split(r"[,\u2013-]", nums_str)
        try:
            nums = [int(p.strip()) for p in parts if p.strip()]
        except ValueError:
            return m.group(0)
        if not nums or any(n < 1 or n > ref_count for n in nums):
            return m.group(0)

        # Math-context guard: skip when the numbers look like terms in
        # an equation ("x 2 + y 3."). Only inspect a few chars so a
        # hyphen inside a compound word ("cross-point switches 20-22.")
        # does not disarm a real citation.
        context_before = md[max(0, m.start() - 3):m.start()]
        if re.search(r"[+*/=<>^]", context_before):
            return m.group(0)

        rest_after = md[m.end():]
        next_word_match = re.match(r"\s*([a-zA-Z]+)", rest_after)
        if next_word_match:
            nw = next_word_match.group(1).lower()
            if nw in _UNITS:
                return m.group(0)
            if post in (" ", "") and nw[0].islower():
                return m.group(0)

        return f"{m.group('pre')} [{nums_str}]{post}"

    return _REF_RE.sub(_replace, md)


# ---------------------------------------------------------------------------
# Bibliography counting
# ---------------------------------------------------------------------------


_REF_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+(?:references|bibliography|works cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Numbered-item patterns: "1." / "1)" / "[1]" at line start, optionally
# preceded by a markdown bullet (``- 1.``, ``* 1.``, ``• 1.``). Marker
# emits bibliography entries as bulleted lists, so the bullet prefix is
# mandatory-compatible rather than a leniency tweak.
_NUMBERED_ITEM_RE = re.compile(
    r"^\s*(?:[-*+\u2022]\s+)?(?:\[\d{1,3}\]|\d{1,3}[.)])\s+"
)

# Minimum consecutive numbered items we accept as a bibliography cluster
# in the heading-free fallback. Five is high enough to reject numbered
# step lists in a methods section but low enough to accept short papers.
_MIN_CLUSTER_SIZE = 5


def count_ref_list_items_from_md(md: str) -> int:
    """Count bibliography entries in a markdown body.

    Primary path: scan lines after the last ``# References`` /
    ``# Bibliography`` heading, counting numbered entries (``1.`` /
    ``1)`` / ``[1]`` / ``- 1.``). Used by parsers that lack structured
    document access (e.g. Marker) so the bracket-restore passes can
    validate ordinal ranges.

    Fallback: when no heading is present, return the size of the
    longest trailing cluster of numbered lines in the document. This
    handles Marker outputs where the heading was eaten during layout
    analysis but the numbered bibliography survives.
    """
    if not md:
        return 0
    matches = list(_REF_HEADING_RE.finditer(md))
    if matches:
        tail = md[matches[-1].end():]
        n = sum(
            1 for line in tail.splitlines()
            if _NUMBERED_ITEM_RE.match(line)
        )
        if n > 0:
            return n
    return _trailing_numbered_cluster(md)


def _trailing_numbered_cluster(md: str) -> int:
    """Longest consecutive run of numbered lines ending near the document tail.

    Iterates lines bottom-up to find the last run of numbered items and
    returns its length if it meets the minimum cluster size. Blank
    lines are allowed inside the cluster so two-line references do not
    abort the count.
    """
    lines = md.splitlines()
    count = 0
    best = 0
    blank_streak = 0
    for line in reversed(lines):
        if _NUMBERED_ITEM_RE.match(line):
            count += 1
            blank_streak = 0
            best = max(best, count)
        elif not line.strip():
            # A single blank line inside a numbered list is fine; two
            # blanks in a row break the cluster.
            blank_streak += 1
            if blank_streak > 1:
                if best >= _MIN_CLUSTER_SIZE:
                    return best
                count = 0
                blank_streak = 0
        else:
            if best >= _MIN_CLUSTER_SIZE:
                return best
            count = 0
            blank_streak = 0
    return best if best >= _MIN_CLUSTER_SIZE else 0
