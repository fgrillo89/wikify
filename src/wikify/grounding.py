"""Canonical grounding-match normalization.

One normalizer shared by the draft validator (`bundle/draft/validator.py`)
and the data-harvest verifier (`data/verify.py`) so a quote grounds
identically at both gates. The dossier renders chunk text for humans —
collapsing whitespace, turning OCR control characters into spaces, and
dropping inline numeric citation markers (``[12]`` / ``[1-3]`` / ``[ 101 ]``).
A quote copied from that readable view must ground against the raw
``chunk_text`` at *either* gate without an extra pass.

This removes rendering noise only — whitespace, control chars, citation
brackets — never content: the quote's words must still appear in order. It is
not a fabrication loophole. The data verifier additionally requires the
reported number in both quote and source (`number_supported`), an independent
check this normalizer does not touch.
"""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_INLINE_CITE_RE = re.compile(r"\[[\d\s,‒–—.\-]+\]")


def normalize_grounding_text(s: str) -> str:
    """Collapse whitespace, strip control chars + inline citation markers, lower."""
    s = _CTRL_RE.sub(" ", s or "")
    s = _INLINE_CITE_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip().lower()


def is_grounded(quote: str, source: str) -> bool:
    """True if *quote* is grounded in *source*: exact substring, else a match
    after shared normalization (whitespace / control chars / citation markers)."""
    if not quote or not source:
        return False
    if quote in source:
        return True
    return normalize_grounding_text(quote) in normalize_grounding_text(source)
