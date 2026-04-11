"""Tolerant text normalization for quote-in-chunk substring matching.

Real PDF-extracted chunks (via pymupdf4llm) contain artifacts that the
model legitimately cleans when emitting a verbatim quote: ``[1]``
citation markers, ``[token][bracket][wrap]`` column-reconstruction
artifacts, double spaces, Unicode dashes and curly quotes. A strict
``in`` check against the raw chunk rejects those quotes even though
they're faithful.

This helper normalizes *only for comparison*. The verbatim form of the
quote is still stored on the concept.
"""

import re
import unicodedata

# All dash variants observed in pymupdf output.
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212-"
_DASH_RE = re.compile(f"[{re.escape(_DASHES)}]")

# Curly / typographic quotes -> straight.
_CURLY_SINGLE = "\u2018\u2019\u201a\u201b"
_CURLY_DOUBLE = "\u201c\u201d\u201e\u201f"

# [12] or [12-15] inline citation markers.
_CITE_RE = re.compile(r"\[\d+(?:-\d+)?\]")

# [word] bracket-wrapping artifact: lowercase ASCII token of length >= 2
# OR a run of digits. We deliberately do NOT match single letters like
# ``[a]`` / ``[b]`` because those are legitimate subfigure refs.
_BRACKET_WRAP_RE = re.compile(r"\[([a-z0-9]{2,})\]")

_WS_RE = re.compile(r"\s+")

# Markdown emphasis markers (`**bold**`, `*italic*`, `_italic_`,
# `__bold__`). The model strips these when emitting a clean quote;
# the raw chunk keeps them. Strip on both sides for comparison.
_MD_EMPHASIS_RE = re.compile(r"[*_]+")

# After dash normalization, collapse whitespace around '-' so
# ``chua - a`` and ``chua-a`` compare equal. This matters because the
# model often respaces an em-dash to `` - `` when cleaning, while the
# raw chunk keeps the tight ``chua\u2014a`` form.
_DASH_WS_RE = re.compile(r"\s*-\s*")


def normalize_for_substring(s: str) -> str:
    """Normalize text for tolerant substring matching against noisy
    PDF-extracted chunks. Preserves the *information* in the string
    while erasing artifacts the model legitimately cleans up.
    """
    # 1. NFKC unicode normalization
    s = unicodedata.normalize("NFKC", s)
    # 2. Dash variants -> ASCII '-'
    s = _DASH_RE.sub("-", s)
    # 3. Curly quotes -> straight
    for ch in _CURLY_SINGLE:
        s = s.replace(ch, "'")
    for ch in _CURLY_DOUBLE:
        s = s.replace(ch, '"')
    # 4. Strip [NN] / [NN-NN] citation markers
    s = _CITE_RE.sub("", s)
    # 5. Unwrap [token] bracket-wrap artifacts (lowercase word/digits, >=2)
    s = _BRACKET_WRAP_RE.sub(r"\1", s)
    # 6. Strip markdown emphasis markers (**bold**, _italic_, etc.)
    s = _MD_EMPHASIS_RE.sub("", s)
    # 7. Collapse whitespace
    s = _WS_RE.sub(" ", s).strip()
    # 8. Collapse whitespace around hyphens
    s = _DASH_WS_RE.sub("-", s)
    # 9. Lowercase
    return s.lower()
